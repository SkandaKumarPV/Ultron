"""
ULTRON Super Monitor - Disaster Monitor
Monitors natural disasters from USGS, NOAA, and other sources.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Dict
from enum import Enum
import requests
import asyncio

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class DisasterType(Enum):
    """Types of natural disasters."""
    EARTHQUAKE = "earthquake"
    VOLCANO = "volcano"
    TSUNAMI = "tsunami"
    HURRICANE = "hurricane"
    WILDFIRE = "wildfire"
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    TORNADO = "tornado"
    DROUGHT = "drought"
    EXTREME_TEMP = "extreme_temp"
    AVALANCHE = "avalanche"
    CYCLONE = "cyclone"


class DisasterSeverity(Enum):
    """Disaster severity levels."""
    UNKNOWN = "unknown"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"
    CATASTROPHIC = "catastrophic"


@dataclass
class DisasterEvent:
    """A disaster event."""
    id: str
    type: DisasterType
    severity: DisasterSeverity
    title: str
    description: str
    location: str
    started: datetime
    updated: datetime
    coordinates: Optional[tuple] = None  # (lat, lon)
    country: Optional[str] = None
    region: Optional[str] = None
    magnitude: Optional[float] = None  # For earthquakes
    depth_km: Optional[float] = None
    magnitude_scale: Optional[str] = None  # Richter, Moment magnitude, etc.
    ended: Optional[datetime] = None
    casualties: Optional[int] = None
    affected_population: Optional[int] = None
    economic_loss_usd: Optional[float] = None
    sources: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True


class DisasterMonitor:
    """
    Monitors natural disasters from multiple sources:
    - USGS Earthquake Hazards Program
    - NOAA/NWS for hurricanes, storms, floods
    - NASA FIRMS for wildfires
    - USGS Volcano Hazards Program
    - GDACS (Global Disaster Alert and Coordination System)
    - NOAA/NWS for hurricanes, floods, tornadoes
    """

    def __init__(self, 
                 disaster_types: List[str] = None,
                 update_interval_minutes: int = 30):
        self.disaster_types = [DisasterType(t) for t in disaster_types] if disaster_types else list(DisasterType)
        self.update_interval = update_interval_minutes * 60
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Cache
        self._disasters: Dict[str, DisasterEvent] = {}  # id -> DisasterEvent
        self._last_fetch = {}
        
        # API endpoints
        self.sources = {
            DisasterType.EARTHQUAKE: [
                "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
                "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson",
            ],
            DisasterType.VOLCANO: [
                "https://volcanoes.usgs.gov/vhp/api/volcanoes.json",
            ],
            DisasterType.HURRICANE: [
                "https://www.nhc.noaa.gov/CurrentStorms.json",
            ],
            DisasterType.WILDFIRE: [
                "https://firms.modaps.eosdis.nasa.gov/api/area/csv/[MAP_KEY]/VIIRS_SNPP_NRT/world/1",
            ],
            DisasterType.FLOOD: [
                "https://api.weather.gov/alerts/active?event=Flood%20Warning",
                "https://api.weather.gov/alerts/active?event=Flash%20Flood%20Warning",
            ],
        }
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.update_interval = 30 * 60  # 30 minutes
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Start disaster monitoring."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Disaster Monitor started")

    def stop(self):
        """Stop disaster monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                asyncio.get_event_loop().run_until_complete(self._task)
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self.fetch_all()
            except Exception as e:
                logger.error(f"Disaster monitor error: {e}")
            
            await asyncio.sleep(self.update_interval)

    async def fetch_all(self):
        """Fetch all disaster types."""
        for disaster_type in self.disaster_types:
            if disaster_type in self.sources:
                for url in self.sources[disaster_type]:
                    await self._fetch_source(disaster_type, url)
                    await asyncio.sleep(1)  # Be nice to APIs

    async def _fetch_source(self, disaster_type: DisasterType, url: str):
        """Fetch data from a single source."""
        try:
            if disaster_type == DisasterType.EARTHQUAKE:
                await self._fetch_usgs_earthquakes(url)
            elif disaster_type == DisasterType.VOLCANO:
                await self._fetch_usgs_volcanoes(url)
            elif disaster_type == DisasterType.HURRICANE:
                await self._fetch_hurricanes(url)
            elif disaster_type == DisasterType.WILDFIRE:
                await self._fetch_wildfires(url)
            elif disaster_type == DisasterType.FLOOD:
                await self._fetch_floods(url)
        except Exception as e:
            logger.error(f"Error fetching {disaster_type.value} from {url}: {e}")

    async def _fetch_usgs_earthquakes(self, url: str):
        """Fetch earthquakes from USGS GeoJSON feed."""
        try:
            response = await asyncio.to_thread(
                requests.get, url, timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            for feature in data.get("features", []):
                props = feature.get("properties", {})
                geom = feature.get("geometry", {})
                coords = geom.get("coordinates", [0, 0, 0])
                
                event = DisasterEvent(
                    id=feature.get("id", ""),
                    type=DisasterType.EARTHQUAKE,
                    severity=self._magnitude_to_severity(props.get("mag", 0)),
                    title=f"M{props.get('mag', '?')} - {props.get('place', 'Unknown')}",
                    description=f"Earthquake at {props.get('place', 'Unknown location')}",
                    location=props.get("place", "Unknown"),
                    coordinates=(coords[1], coords[0]) if len(coords) >= 2 else None,
                    magnitude=props.get("mag"),
                    depth_km=coords[2] if len(coords) >= 3 else None,
                    magnitude_scale="moment" if props.get("magType") == "mww" else props.get("magType"),
                    started=datetime.fromtimestamp(props.get("time", 0) / 1000),
                    updated=datetime.fromtimestamp(props.get("updated", 0) / 1000) if props.get("updated") else datetime.now(),
                    sources=[f"USGS:{feature.get('id', '')}"],
                    metadata={"usgs_data": props},
                )
                
                self._add_or_update_disaster(event)
                
        except Exception as e:
            logger.error(f"Error fetching USGS earthquakes: {e}")

    def _magnitude_to_severity(self, magnitude: float) -> DisasterSeverity:
        """Convert earthquake magnitude to severity."""
        if magnitude is None:
            return DisasterSeverity.UNKNOWN
        if magnitude >= 8.0:
            return DisasterSeverity.CATASTROPHIC
        elif magnitude >= 7.0:
            return DisasterSeverity.EXTREME
        elif magnitude >= 6.0:
            return DisasterSeverity.SEVERE
        elif magnitude >= 5.0:
            return DisasterSeverity.MODERATE
        elif magnitude >= 4.0:
            return DisasterSeverity.MINOR
        else:
            return DisasterSeverity.MINOR

    async def _fetch_usgs_volcanoes(self, url: str):
        """Fetch volcano data from USGS."""
        try:
            response = await asyncio.to_thread(requests.get, url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for volcano in data.get("volcanoes", []):
                if volcano.get("status") in ["erupting", "unrest"]:
                    event = DisasterEvent(
                        id=f"volcano_{volcano.get('volcano_number', '')}",
                        type=DisasterType.VOLCANO,
                        severity=DisasterSeverity.SEVERE if volcano.get("status") == "erupting" else DisasterSeverity.MODERATE,
                        title=f"{volcano.get('volcano_name', 'Unknown')} - {volcano.get('status', 'unrest')}",
                        description=volcano.get("activity_description", "Volcanic activity detected"),
                        location=volcano.get("volcano_name", "Unknown"),
                        coordinates=(volcano.get("latitude"), volcano.get("longitude")) if volcano.get("latitude") and volcano.get("longitude") else None,
                        country=volcano.get("country"),
                        started=datetime.now(),
                        updated=datetime.now(),
                        sources=[f"USGS:{volcano.get('volcano_number', '')}"],
                        metadata={"usgs_data": volcano},
                        is_active=True,
                    )
                    self._add_or_update_disaster(event)
                    
        except Exception as e:
            logger.error(f"Error fetching volcano data: {e}")

    async def _fetch_hurricanes(self, url: str):
        """Fetch hurricane data from NHC."""
        try:
            response = await asyncio.to_thread(requests.get, url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for storm in data.get("storms", []):
                # Parse NHC data
                event = DisasterEvent(
                    id=f"nhc_{storm.get('id', '')}",
                    type=DisasterType.HURRICANE,
                    severity=self._storm_to_severity(storm),
                    title=storm.get("name", "Unnamed Storm"),
                    description=f"{storm.get('type', 'Tropical')} cyclone",
                    location=storm.get("location", "Atlantic"),
                    started=datetime.now(),
                    updated=datetime.now(),
                    sources=[f"NHC:{storm.get('id', '')}"],
                    metadata={"nhc_data": storm},
                    is_active=True,
                )
                self._add_or_update_disaster(event)
                
        except Exception as e:
            logger.error(f"Error fetching hurricane data: {e}")

    def _storm_to_severity(self, storm: dict) -> DisasterSeverity:
        """Convert storm type to severity."""
        storm_type = storm.get("type", "").lower()
        if "hurricane" in storm_type:
            category = storm.get("category", 0)
            if category >= 4:
                return DisasterSeverity.CATASTROPHIC
            elif category >= 3:
                return DisasterSeverity.EXTREME
            elif category >= 1:
                return DisasterSeverity.SEVERE
        elif "tropical storm" in storm_type:
            return DisasterSeverity.MODERATE
        elif "tropical depression" in storm_type:
            return DisasterSeverity.MINOR
        return DisasterSeverity.UNKNOWN

    async def _fetch_wildfires(self, url: str):
        """Fetch wildfire data from NASA FIRMS."""
        # NASA FIRMS requires API key and specific format
        # Placeholder for implementation
        pass

    async def _fetch_floods(self, url: str):
        """Fetch flood alerts from NOAA/NWS."""
        try:
            response = await asyncio.to_thread(requests.get, url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for feature in data.get("features", []):
                props = feature.get("properties", {})
                
                event = DisasterEvent(
                    id=feature.get("id", ""),
                    type=DisasterType.FLOOD,
                    severity=self._alert_to_severity(props.get("severity", "")),
                    title=props.get("event", "Flood Alert"),
                    description=props.get("description", ""),
                    location=props.get("areaDesc", "Unknown"),
                    started=datetime.fromisoformat(props.get("sent", "").replace("Z", "+00:00")) if props.get("sent") else datetime.now(),
                    updated=datetime.fromisoformat(props.get("updated", "").replace("Z", "+00:00")) if props.get("updated") else datetime.now(),
                    ended=datetime.fromisoformat(props.get("expires", "").replace("Z", "+00:00")) if props.get("expires") else None,
                    sources=[f"NWS:{feature.get('id', '')}"],
                    metadata={"nws_data": props},
                    is_active=props.get("status") == "Actual",
                )
                self._add_or_update_disaster(event)
                
        except Exception as e:
            logger.error(f"Error fetching flood data: {e}")

    def _alert_to_severity(self, severity: str) -> DisasterSeverity:
        """Convert NWS severity to disaster severity."""
        mapping = {
            "extreme": DisasterSeverity.EXTREME,
            "severe": DisasterSeverity.SEVERE,
            "moderate": DisasterSeverity.MODERATE,
            "minor": DisasterSeverity.MINOR,
        }
        return mapping.get(severity.lower(), DisasterSeverity.UNKNOWN)

    def _add_or_update_disaster(self, event: DisasterEvent):
        """Add or update a disaster event."""
        existing = self._disasters.get(event.id)
        
        if existing:
            # Update existing
            existing.updated = event.updated
            existing.is_active = event.is_active
            existing.metadata.update(event.metadata)
            if event.severity.value > existing.severity.value:
                existing.severity = event.severity
                existing.description = event.description
        else:
            self._disasters[event.id] = event
            self._last_fetch[event.id] = datetime.now()

    def get_disasters(self, 
                      disaster_type: Optional[DisasterType] = None,
                      active_only: bool = True,
                      region: Optional[str] = None,
                      severity: Optional[DisasterSeverity] = None,
                      since: Optional[datetime] = None,
                      limit: int = 100) -> List[DisasterEvent]:
        """Get filtered disaster events."""
        events = list(self._disasters.values())
        
        if active_only:
            events = [e for e in events if e.is_active]
        if disaster_type:
            events = [e for e in events if e.type == disaster_type]
        if region:
            events = [e for e in events if e.region == region or 
                     (e.country and region.lower() in e.country.lower())]
        if severity:
            events = [e for e in events if e.severity == severity]
        if since:
            events = [e for e in events if e.updated >= since]
        
        # Sort by updated descending
        events.sort(key=lambda e: e.updated, reverse=True)
        
        return events[:limit]

    def get_active_disasters(self) -> List[DisasterEvent]:
        """Get all currently active disasters."""
        return self.get_disasters(active_only=True)

    def get_disaster(self, disaster_id: str) -> Optional[DisasterEvent]:
        """Get a specific disaster by ID."""
        return self._disasters.get(disaster_id)

    def get_active_alerts(self) -> List[DisasterEvent]:
        """Get all active high-severity disasters."""
        events = self.get_disasters(active_only=True)
        return [e for e in events if e.severity in [DisasterSeverity.SEVERE, DisasterSeverity.EXTREME, DisasterSeverity.CATASTROPHIC]]

    def get_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        all_events = list(self._disasters.values())
        active = [e for e in all_events if e.is_active]
        
        by_type = {}
        by_severity = {}
        by_country = {}
        
        for event in active:
            by_type[event.type.value] = by_type.get(event.type.value, 0) + 1
            by_severity[event.severity.value] = by_severity.get(event.severity.value, 0) + 1
            if event.country:
                by_country[event.country] = by_country.get(event.country, 0) + 1
        
        return {
            "total_events": len(all_events),
            "active_events": len(active),
            "by_type": by_type,
            "by_severity": by_severity,
            "by_country": by_country,
            "last_update": max([e.updated for e in all_events], default=None),
        }

    def get_status(self) -> Dict[str, Any]:
        """Get monitor status."""
        return {
            "running": self._running,
            "update_interval_minutes": self.update_interval / 60,
            "total_disasters": len(self._disasters),
            "active_disasters": len([e for e in self._disasters.values() if e.is_active]),
            "last_fetch": max(self._last_fetch.values()) if self._last_fetch else None,
        }