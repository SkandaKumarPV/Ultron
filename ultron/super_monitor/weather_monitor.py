"""
ULTRON Super Monitor - Weather Monitor
Monitors weather conditions and alerts using NOAA and other weather APIs.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Dict
from enum import Enum
import requests
import asyncio
import aiohttp

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class WeatherSeverity(Enum):
    """Weather alert severity levels."""
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"


@dataclass
class WeatherLocation:
    """Location for weather monitoring."""
    name: str
    lat: float
    lon: float
    timezone: str = "UTC"
    alerts_enabled: bool = True
    forecast_hours: int = 48


@dataclass
class WeatherAlert:
    """Weather alert/event."""
    id: str
    location: str
    severity: WeatherSeverity
    event: str
    description: str
    areas: List[str]
    issued: datetime
    expires: Optional[datetime]
    instructions: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WeatherForecast:
    """Weather forecast for a location."""
    location: str
    timestamp: datetime
    temperature: float  # Celsius
    feels_like: float
    humidity: int
    pressure: float  # hPa
    wind_speed: float  # m/s
    wind_direction: int  # degrees
    weather_code: int
    weather_description: str
    precipitation_probability: float
    precipitation_amount: float  # mm
    visibility: float  # km
    uv_index: float
    cloud_cover: int  # percentage


class WeatherMonitor:
    """
    Monitors weather conditions and alerts using NOAA NWS API and OpenWeatherMap.
    """

    def __init__(self, 
                 locations: List[Dict[str, Any]],
                 api_key: Optional[str] = None,
                 update_interval_minutes: int = 15):
        self.locations = [self._parse_location(loc) for loc in locations]
        self.api_key = api_key or config.config.get("weather", {}).get("api_key")
        self.update_interval = update_interval_minutes * 60
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Cache
        self._alerts_cache: Dict[str, List[WeatherAlert]] = {}
        self._forecast_cache: Dict[str, List[WeatherForecast]] = {}
        self._last_fetch: Dict[str, datetime] = {}
        
        # NOAA NWS API base
        self.nws_base = "https://api.weather.gov"
        self.owm_base = "https://api.openweathermap.org/data/2.5"
        
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def _parse_location(self, loc: Dict[str, Any]) -> dict:
        """Parse location configuration."""
        return {
            "name": loc.get("name", "Unknown"),
            "lat": float(loc.get("lat", 0)),
            "lon": float(loc.get("lon", 0)),
            "timezone": loc.get("timezone", "UTC"),
            "alerts_enabled": loc.get("alerts_enabled", True),
            "forecast_hours": loc.get("forecast_hours", 48),
        }

    async def start(self):
        """Start weather monitoring."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Weather Monitor started")

    async def stop(self):
        """Stop weather monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        """Main monitoring loop."""
        while True:
            if not self._running:
                break
            
            try:
                await self._fetch_all_locations()
            except Exception as e:
                logger.error(f"Weather fetch error: {e}")
            
            await asyncio.sleep(self.update_interval)

    async def _fetch_all_locations(self):
        """Fetch weather for all monitored locations."""
        tasks = []
        for location in self.locations:
            tasks.append(self._fetch_location_weather(location))
        
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_location_weather(self, location: dict):
        """Fetch weather for a single location."""
        # Try NOAA NWS first (US only), then OpenWeatherMap
        try:
            alerts = await self._fetch_nws_alerts(location)
            if alerts:
                self._alerts_cache[location["name"]] = alerts
            
            forecasts = await self._fetch_forecast(location)
            if forecasts:
                self._forecast_cache[location["name"]] = forecasts
            
            self._last_fetch[location["name"]] = datetime.now()
            
        except Exception as e:
            logger.error(f"Error fetching weather for {location['name']}: {e}")

    async def _fetch_nws_alerts(self, location: dict) -> List[Dict]:
        """Fetch alerts from NOAA NWS API."""
        alerts = []
        
        try:
            # Get zone/point for location
            point_url = f"{self.nws_base}/points/{location['lat']},{location['lon']}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(point_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        point_data = await resp.json()
                        
                        # Get alerts for zone
                        zone_url = point_data.get("properties", {}).get("forecastZone")
                        if zone_url:
                            zone_id = zone_url.split("/")[-1]
                            alerts_url = f"{self.nws_base}/alerts/active?zone={zone_id}"
                            
                            async with session.get(alerts_url, timeout=aiohttp.ClientTimeout(total=30)) as alert_resp:
                                if alert_resp.status == 200:
                                    alert_data = await alert_resp.json()
                                    
                                    for feature in alert_data.get("features", []):
                                        props = feature.get("properties", {})
                                        alert = {
                                            "id": feature.get("id", ""),
                                            "event": props.get("event", ""),
                                            "severity": props.get("severity", "unknown"),
                                            "certainty": props.get("certainty", ""),
                                            "urgency": props.get("urgency", ""),
                                            "areas": props.get("areaDesc", "").split("; "),
                                            "description": props.get("description", ""),
                                            "instruction": props.get("instruction", ""),
                                            "issued": props.get("sent", ""),
                                            "expires": props.get("expires", ""),
                                            "metadata": props,
                                        }
                                        if self._is_relevant_alert(alert, location):
                                            yield alert
        except Exception as e:
            logger.error(f"NWS alerts error for {location['name']}: {e}")

        return

    def _is_relevant_alert(self, alert: Dict, location: dict) -> bool:
        """Check if alert is relevant to our location."""
        if not alert.get("areas"):
            return True
        
        # Check if location name or nearby areas mentioned
        location_name = location["name"].lower()
        for area in alert.get("areas", []):
            if location_name in area.lower() or any(
                keyword in area.lower() for keyword in ["county", "zone", "area"]
            ):
                return True
        return False

    async def _fetch_forecast(self, location: dict) -> List[Dict]:
        """Fetch weather forecast from OpenWeatherMap."""
        if not self.api_key:
            return []
        
        try:
            url = f"{self.owm_base}/forecast"
            params = {
                "lat": location["lat"],
                "lon": location["lon"],
                "appid": self.api_key,
                "units": "metric",
                "cnt": min(location.get("forecast_hours", 48) // 3 + 1, 40),
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.owm_base + "/forecast", params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._parse_owm_forecast(data, location)
        except Exception as e:
            logger.error(f"OWM forecast error for {location['name']}: {e}")
        
        return []

    def _parse_owm_forecast(self, data: dict, location: dict) -> List[Dict]:
        """Parse OpenWeatherMap forecast response."""
        forecasts = []
        
        for item in data.get("list", []):
            try:
                forecast = {
                    "location": location["name"],
                    "timestamp": datetime.fromtimestamp(item["dt"]),
                    "temperature": item["main"]["temp"],
                    "feels_like": item["main"]["feels_like"],
                    "humidity": item["main"]["humidity"],
                    "pressure": item["main"]["pressure"],
                    "wind_speed": item["wind"]["speed"],
                    "wind_direction": item["wind"].get("deg", 0),
                    "weather_code": item["weather"][0]["id"],
                    "weather_description": item["weather"][0]["description"],
                    "precipitation_probability": item.get("pop", 0),
                    "precipitation_amount": item.get("rain", {}).get("3h", 0) + item.get("snow", {}).get("3h", 0),
                    "visibility": item.get("visibility", 10000) / 1000,  # km
                    "uv_index": item.get("uvi", 0),
                    "cloud_cover": item["clouds"]["all"],
                }
                forecasts.append(forecast)
            except Exception:
                continue
        
        return forecasts

    def get_weather(self, location_name: str) -> Optional[Dict]:
        """Get current weather for a location."""
        # Return latest cached data
        alerts = self._alerts_cache.get(location_name, [])
        forecasts = self._forecast_cache.get(location_name, [])
        
        return {
            "location": location_name,
            "alerts": alerts,
            "forecasts": forecasts[:24] if forecasts else [],  # Next 24 hours
            "last_update": self._last_fetch.get(location_name),
        }

    def get_alerts(self, location_name: Optional[str] = None, 
                   severity: Optional[str] = None) -> List[Dict]:
        """Get active alerts."""
        if location_name:
            alerts = self._alerts_cache.get(location_name, [])
        else:
            alerts = []
            for loc_alerts in self._alerts_cache.values():
                alerts.extend(loc_alerts)
        
        if severity:
            alerts = [a for a in alerts if a.get("severity", "").lower() == severity.lower()]
        
        return alerts

    def get_forecast(self, location_name: str, hours: int = 48) -> List[Dict]:
        """Get forecast for location."""
        forecasts = self._forecast_cache.get(location_name, [])
        return [f for f in forecasts if (f["timestamp"] - datetime.now()).total_seconds() <= hours * 3600]

    def get_active_alerts(self) -> List[Dict]:
        """Get all active alerts across all locations."""
        all_alerts = []
        for location_alerts in self._alerts_cache.values():
            all_alerts.extend(location_alerts)
        return all_alerts

    async def stop(self):
        """Stop weather monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# Simple aiohttp-like fallback for environments without aiohttp
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    
    # Simple requests-based fallback
    class AsyncSessionFallback:
        def __init__(self):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def get(self, url, timeout=30, params=None):
            import requests
            response = requests.get(url, params=params, timeout=timeout)
            return SimpleResponse(response)

    class SimpleResponse:
        def __init__(self, response):
            self._response = response
            self.status = response.status_code
        
        async def json(self):
            return self._response.json()
        
        @property
        def status(self):
            return self._response.status_code

    aiohttp = type('aiohttp', (), {
        'ClientSession': AsyncSessionFallback,
        'ClientTimeout': lambda total: type('Timeout', (), {'total': 30})()
    })()

# Re-export
from .weather_monitor import WeatherMonitor, WeatherAlert, WeatherForecast, WeatherLocation, WeatherSeverity