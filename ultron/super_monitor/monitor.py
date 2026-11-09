"""
ULTRON Super Monitor - Main Coordinator
Coordinates all Super Monitor sub-components for public-source intelligence monitoring.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from pathlib import Path
from enum import Enum

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class MonitorStatus(Enum):
    """Super Monitor status states."""
    DISABLED = "disabled"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class SuperMonitorConfig:
    """Configuration for Super Monitor."""
    enabled: bool = False
    update_interval_minutes: int = 30
    max_events_stored: int = 10000
    max_events_per_source: int = 100
    data_dir: str = "super_monitor_data"
    enable_news: bool = True
    enable_weather: bool = True
    enable_disasters: bool = True
    enable_spatial: bool = True
    enable_timeline: bool = True
    enable_evidence: bool = True
    enable_freshness: bool = True
    news_sources: List[str] = field(default_factory=lambda: [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://feeds.apnews.com/rss/apf-topnews",
        "https://feeds.npr.org/1001/rss.xml",
        "https://www.theguardian.com/world/rss",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    ])
    weather_locations: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"name": "Global", "lat": 0, "lon": 0},
    ])
    disaster_types: List[str] = field(default_factory=lambda: [
        "earthquake", "volcano", "tsunami", "hurricane", "wildfire", "flood"
    ])


class SuperMonitor:
    """
    Main Super Monitor coordinator.
    Coordinates all public-source intelligence monitoring components.
    """

    def __init__(self, config: Optional[SuperMonitorConfig] = None):
        self.config = config or SuperMonitorConfig()
        self._status = MonitorStatus.DISABLED
        self._initialized = False
        self._components: Dict[str, Any] = {}
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._event_buffer: List[Dict] = []
        self._evidence_store: Dict[str, List] = {}
        self._start_time: Optional[datetime] = None
        self._last_update: Optional[datetime] = None

        # Initialize components lazily
        self._news_aggregator = None
        self._weather_monitor = None
        self._disaster_monitor = None
        self._spatial_layers = None
        self._event_timeline = None
        self._evidence_organizer = None
        self._freshness_tracker = None

    @property
    def status(self) -> MonitorStatus:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._running

    def initialize(self) -> bool:
        """Initialize all Super Monitor components."""
        if self._initialized:
            return True

        if not self.config.enabled:
            logger.info("Super Monitor is disabled in config")
            self._status = MonitorStatus.DISABLED
            return False

        try:
            self._status = MonitorStatus.INITIALIZING
            
            # Create data directory
            data_dir = Path(self.config.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)

            # Initialize components based on config
            if self.config.enable_news:
                from .news_aggregator import NewsAggregator
                self._news_aggregator = NewsAggregator(
                    sources=self.config.news_sources,
                    max_events_per_source=self.config.max_events_per_source,
                )

            if self.config.enable_weather:
                from .weather_monitor import WeatherMonitor
                self._weather_monitor = WeatherMonitor(
                    locations=self.config.weather_locations,
                )

            if self.config.enable_disasters:
                from .disaster_monitor import DisasterMonitor
                self._disaster_monitor = DisasterMonitor(
                    disaster_types=self.config.disaster_types,
                )

            if self.config.enable_spatial:
                from .spatial_layers import SpatialLayerManager
                self._spatial_layers = SpatialLayerManager()

            if self.config.enable_timeline:
                from .event_timeline import EventTimeline
                self._event_timeline = EventTimeline(
                    max_events=self.config.max_events_stored,
                )

            if self.config.enable_evidence:
                from .evidence_organizer import EvidenceOrganizer
                self._evidence_organizer = EvidenceOrganizer()

            if self.config.enable_freshness:
                from .source_freshness import SourceFreshnessTracker
                self._freshness_tracker = SourceFreshnessTracker()

            self._initialized = True
            self._status = MonitorStatus.ACTIVE
            self._start_time = datetime.now()
            
            audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                      details={"action": "super_monitor_initialized", "components": list(self._components.keys())})
            
            logger.info("Super Monitor initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Super Monitor: {e}")
            self._status = MonitorStatus.ERROR
            return False

    async def start(self) -> bool:
        """Start all monitoring tasks."""
        if not self._initialized:
            if not self.initialize():
                return False

        if self._running:
            return True

        if not self.config.enabled:
            return False

        try:
            self._running = True
            self._status = MonitorStatus.ACTIVE

            # Start all component tasks
            tasks = []

            if self._news_aggregator:
                self._tasks.append(asyncio.create_task(self._news_aggregator.run()))
            
            if self._weather_monitor:
                self._tasks.append(asyncio.create_task(self._weather_monitor.run()))
            
            if self._disaster_monitor:
                self._tasks.append(asyncio.create_task(self._disaster_monitor.run()))
            
            if self._freshness_tracker:
                self._tasks.append(asyncio.create_task(self._freshness_tracker.run()))

            # Start periodic update task
            self._tasks.append(asyncio.create_task(self._periodic_update()))

            audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                      details={"action": "super_monitor_started", "tasks": len(self._tasks)})

            logger.info("Super Monitor started")
            return True

        except Exception as e:
            logger.error(f"Failed to start Super Monitor: {e}")
            self._status = MonitorStatus.ERROR
            self._running = False
            return False

    async def stop(self) -> bool:
        """Stop all monitoring tasks."""
        if not self._running:
            return True

        self._running = False
        self._status = MonitorStatus.PAUSED

        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()

        audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                  details={"action": "super_monitor_stopped"})

        logger.info("Super Monitor stopped")
        return True

    async def _periodic_update(self):
        """Periodic update task for all components."""
        while self._running:
            try:
                await asyncio.sleep(self.config.update_interval_minutes * 60)
                
                if not self._running:
                    break

                # Trigger updates for all components
                await self._update_all_components()
                self._last_update = datetime.now()
                
                # Prune old events
                self._prune_old_events()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic update error: {e}")

    async def _update_all_components(self):
        """Trigger updates for all active components."""
        tasks = []

        if self._news_aggregator:
            tasks.append(self._news_aggregator.fetch_all())
        
        if self._weather_monitor:
            tasks.append(self._weather_monitor.fetch_all())
        
        if self._disaster_monitor:
            tasks.append(self._disaster_monitor.fetch_all())
        
        if self._freshness_tracker:
            tasks.append(self._freshness_tracker.check_all_sources())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _prune_old_events(self):
        """Remove old events beyond storage limits."""
        # Implemented in individual components
        pass

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status of all components."""
        return {
            "status": self._status.value,
            "running": self._running,
            "initialized": self._initialized,
            "started_at": self._start_time.isoformat() if self._start_time else None,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "uptime_seconds": (datetime.now() - self._start_time).total_seconds() if self._start_time else 0,
            "components": {
                "news": self._news_aggregator is not None,
                "weather": self._weather_monitor is not None,
                "disasters": self._disaster_monitor is not None,
                "spatial": self._spatial_layers is not None,
                "timeline": True,  # Always available
                "evidence": self._evidence_organizer is not None,
                "freshness": self._freshness_tracker is not None,
            },
            "event_buffer_size": len(self._event_buffer),
            "evidence_store_size": sum(len(v) for v in self._evidence_store.values()),
        }

    def get_events(self, 
                   source: Optional[str] = None,
                   event_type: Optional[str] = None,
                   since: Optional[datetime] = None,
                   limit: int = 100) -> List[Dict]:
        """Get events from all sources with optional filters."""
        events = self._event_buffer.copy()
        
        if source:
            events = [e for e in events if e.get("source") == source]
        
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        
        if since:
            events = [e for e in events if e.get("timestamp", datetime.min) >= since]
        
        # Sort by timestamp descending
        events.sort(key=lambda e: e.get("timestamp", datetime.min), reverse=True)
        
        return events[:limit]

    def add_evidence(self, event_id: str, evidence: Dict[str, Any]) -> bool:
        """Add evidence to an event."""
        if self._evidence_organizer:
            return self._evidence_organizer.add_evidence(event_id, evidence)
        return False

    def get_evidence(self, event_id: str) -> List[Dict]:
        """Get all evidence for an event."""
        if self._evidence_organizer:
            return self._evidence_organizer.get_evidence(event_id)
        return []

    def create_timeline(self, 
                       event_ids: List[str],
                       title: str = "Event Timeline") -> Optional[Dict]:
        """Create a timeline from event IDs."""
        if self._event_timeline:
            return self._event_timeline.create_timeline(event_ids, title)
        return None

    def get_map(self, 
                center: Optional[tuple] = None,
                zoom: int = 10,
                layers: Optional[List[str]] = None) -> Optional[str]:
        """Generate an interactive map with specified layers."""
        if self._spatial_layers:
            return self._spatial_layers.generate_map(
                center=center or (0, 0),
                zoom=zoom,
                layers=layers or []
            )
        return None

    def get_weather(self, location: str) -> Optional[Dict]:
        """Get weather for a location."""
        if self._weather_monitor:
            return self._weather_monitor.get_weather(location)
        return None

    def get_disasters(self, 
                      disaster_type: Optional[str] = None,
                      region: Optional[str] = None) -> List[Dict]:
        """Get active disasters."""
        if self._disaster_monitor:
            return self._disaster_monitor.get_disasters(
                disaster_type=disaster_type,
                region=region
            )
        return []

    def get_source_freshness(self, source: str) -> Optional[Dict]:
        """Get freshness info for a source."""
        if self._freshness_tracker:
            return self._freshness_tracker.get_freshness(source)
        return None


# Global instance
_super_monitor: Optional[SuperMonitor] = None


def get_super_monitor() -> SuperMonitor:
    """Get or create the global Super Monitor instance."""
    global _super_monitor
    if _super_monitor is None:
        _super_monitor = SuperMonitor()
    return _super_monitor