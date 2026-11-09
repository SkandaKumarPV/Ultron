"""
ULTRON Super Monitor - Source Freshness Tracker
Tracks freshness and reliability of information sources.
"""

import asyncio
import logging
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Dict
from pathlib import Path
from enum import Enum
import re
from urllib.parse import urlparse

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class FreshnessStatus(Enum):
    """Source freshness status."""
    FRESH = "fresh"           # Updated within expected interval
    STALE = "stale"           # Past expected update interval
    STALE_CRITICAL = "stale_critical"  # Significantly overdue
    UNKNOWN = "unknown"       # No update history
    ERROR = "error"           # Error fetching


class SourceType(Enum):
    """Types of information sources."""
    RSS = "rss"
    API = "api"
    HTML = "html"
    API_KEY = "api_key"
    DATABASE = "database"
    FILE = "file"
    MANUAL = "manual"


@dataclass
class SourceConfig:
    """Configuration for a monitored source."""
    id: str
    name: str
    url: str
    source_type: SourceType = SourceType.RSS
    expected_interval_minutes: int = 60  # Expected update interval
    critical_interval_minutes: int = 1440  # 24 hours - critical threshold
    headers: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    tags: List[str] = field(default_factory=list)
    parser: str = "auto"  # auto, rss, json, html, custom
    custom_parser: Optional[str] = None  # Custom parser function name
    timeout_seconds: int = 30
    verify_ssl: bool = True
    auth: Optional[Dict[str, str]] = None  # auth headers


@dataclass
class FreshnessRecord:
    """Record of a source's freshness check."""
    source_id: str
    timestamp: datetime
    status: FreshnessStatus
    latency_ms: int
    content_hash: Optional[str] = None
    content_length: int = 0
    items_count: int = 0
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class SourceFreshness:
    """Aggregated freshness state for a source."""
    config: SourceConfig
    current_status: FreshnessStatus = FreshnessStatus.UNKNOWN
    last_check: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_content_hash: Optional[str] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_checks: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    last_content_hash: Optional[str] = None
    last_items_count: int = 0
    freshness_history: List[Dict] = field(default_factory=list)  # Last 100 checks


class SourceFreshnessTracker:
    """
    Tracks freshness and reliability of information sources.
    Monitors RSS feeds, APIs, and other sources for freshness and reliability.
    """

    def __init__(self, 
                 storage_dir: Optional[Path] = None,
                 default_interval_minutes: int = 60,
                 default_critical_minutes: int = 1440):
        self.storage_dir = config.resolve_path("freshness")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        self.default_interval = default_interval_minutes
        self.default_critical = default_critical_minutes
        
        self._sources: Dict[str, SourceConfig] = {}
        self._freshness: Dict[str, SourceFreshness] = {}
        self._check_history: List[Dict] = []
        self._max_history = 10000
        self._lock = asyncio.Lock()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Source checkers
        self._checkers = {
            "rss": self._check_rss,
            "api": self._check_api,
            "html": self._check_html,
            "api_key": self._check_api,
        }

    def register_source(self, config: SourceConfig) -> bool:
        """Register a source for freshness tracking."""
        if config.id in self._sources:
            logger.warning(f"Source {config.id} already registered")
            return False
        
        self._sources[config.id] = config
        self._freshness[config.id] = SourceFreshness(config=config)
        return True

    def remove_source(self, source_id: str) -> bool:
        """Remove a source from tracking."""
        if source_id in self._sources:
            del self._sources[source_id]
            if source_id in self._freshness:
                del self._freshness[source_id]
            return True
        return False

    def get_source(self, source_id: str) -> Optional[SourceConfig]:
        return self._sources.get(source_id)

    def get_freshness(self, source_id: str) -> Optional[Dict]:
        """Get freshness status for a source."""
        freshness = self._freshness.get(source_id)
        if not freshness:
            return None
        
        config = self._sources.get(source_id)
        if not config:
            return None
        
        # Calculate time since last check
        time_since_check = None
        if freshness.last_check:
            time_since = datetime.now() - freshness.last_check
            minutes_since = time_since_check.total_seconds() / 60
        else:
            minutes_since = None
        
        # Determine expected status
        expected_interval = self._get_expected_interval(config)
        critical_interval = self._get_critical_interval(config)
        
        if minutes_since is None:
            expected_status = FreshnessStatus.UNKNOWN
        elif minutes_since <= expected_interval:
            expected_status = FreshnessStatus.FRESH
        elif minutes_since <= critical_interval:
            expected_status = FreshnessStatus.STALE
        else:
            expected_status = FreshnessStatus.STALE_CRITICAL
        
        # Overall status is worse of actual and expected
        status_order = {
            FreshnessStatus.FRESH: 0,
            FreshnessStatus.UNKNOWN: 1,
            FreshnessStatus.STALE: 2,
            FreshnessStatus.STALE_CRITICAL: 3,
            FreshnessStatus.ERROR: 4,
        }
        
        actual_rank = status_order.get(freshness.current_status, 0)
        expected_rank = status_order.get(expected_status, 0)
        overall_status = max(freshness.current_status, expected_status, key=lambda s: status_order.get(s, 0))
        
        return {
            "source_id": source_id,
            "name": config.name,
            "url": config.url,
            "current_status": freshness.current_status.value,
            "expected_status": expected_status.value,
            "overall_status": overall_status.value,
            "last_check": freshness.last_check.isoformat() if freshness.last_check else None,
            "last_success": freshness.last_success.isoformat() if freshness.last_success else None,
            "minutes_since_check": minutes_since,
            "expected_interval_minutes": expected_interval,
            "critical_interval_minutes": critical_interval,
            "consecutive_failures": freshness.consecutive_failures,
            "consecutive_successes": freshness.consecutive_successes,
            "total_checks": freshness.total_checks,
            "failure_rate": freshness.total_failures / max(1, freshness.total_checks),
            "avg_latency_ms": freshness.avg_latency_ms,
            "last_content_hash": freshness.last_content_hash,
            "last_items_count": freshness.last_items_count,
            "consecutive_failures": freshness.consecutive_failures,
            "consecutive_successes": freshness.consecutive_successes,
        }

    def get_all_freshness(self) -> List[Dict]:
        """Get freshness status for all sources."""
        return [self.get_freshness(sid) for sid in self._sources.keys()]

    def get_stale_sources(self, critical_only: bool = False) -> List[Dict]:
        """Get sources that are stale or critical."""
        stale = []
        for source_id in self._sources:
            freshness_info = self.get_freshness(source_id)
            if freshness_info:
                status = freshness_info["overall_status"]
                if critical_only and status == FreshnessStatus.STALE_CRITICAL.value:
                    stale.append(freshness_info)
                elif not critical_only and status in [FreshnessStatus.STALE.value, FreshnessStatus.STALE_CRITICAL.value]:
                    stale.append(freshness_info)
        return stale

    def get_overdue_sources(self) -> List[Dict]:
        """Get sources that are overdue for checking."""
        overdue = []
        now = datetime.now()
        
        for source_id, config in self._sources.items():
            freshness = self._freshness.get(source_id)
            if not freshness or not freshness.last_check:
                overdue.append(self.get_freshness(config.id))
                continue
            
            expected_interval = self._get_expected_interval(config)
            time_since_check = (now - freshness.last_check).total_seconds() / 60
            
            if time_since_check > expected_interval * 1.5:  # 50% overdue
                freshness_info = self.get_freshness(config.id)
                if freshness_info:
                    freshness_info["minutes_overdue"] = time_since_check - expected_interval
                    overdue.append(freshness_info)
        
        return overdue

    def _get_expected_interval(self, config: SourceConfig) -> float:
        return float(config.expected_interval_minutes)

    def _get_critical_interval(self, config: SourceConfig) -> float:
        return float(config.critical_interval_minutes)

    async def check_source(self, source_id: str) -> Dict[str, Any]:
        """Manually trigger a freshness check for a source."""
        config = self._sources.get(source_id)
        if not config:
            return {"error": "Source not found"}
        
        if not config.enabled:
            return {"error": "Source disabled"}
        
        return await self._check_source(config)

    async def _check_source(self, config: SourceConfig) -> Dict[str, Any]:
        """Check a single source."""
        start_time = datetime.now()
        freshness = self._freshness[config.id]
        
        try:
            # Dispatch to appropriate checker
            checker = self._checkers.get(config.source_type.value, self._check_generic)
            result = await checker(config)
            
            latency_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            
            # Update freshness record
            freshness.last_check = datetime.now()
            freshness.total_checks += 1
            freshness.avg_latency_ms = (
                (freshness.avg_latency_ms * (freshness.total_checks - 1) + latency_ms) 
                / freshness.total_checks
            )
            
            if result.get("success"):
                freshness.consecutive_failures = 0
                freshness.consecutive_successes += 1
                freshness.last_success = datetime.now()
                freshness.last_content_hash = result.get("content_hash")
                freshness.last_items_count = result.get("items_count", 0)
                
                status = FreshnessStatus.FRESH
            else:
                freshness.consecutive_failures += 1
                freshness.consecutive_successes = 0
                status = FreshnessStatus.ERROR
            
            freshness.current_status = status
            
            # Record history
            record = {
                "timestamp": datetime.now().isoformat(),
                "status": status.value,
                "latency_ms": latency_ms,
                "items_count": result.get("items_count", 0),
                "error": result.get("error"),
            }
            freshness.freshness_history.append(record)
            if len(freshness.freshness_history) > 100:
                freshness.freshness_history = freshness.freshness_history[-100:]
            
            return {
                "success": result.get("success", False),
                "status": status.value,
                "latency_ms": latency_ms,
                "items_count": result.get("items_count", 0),
                "error": result.get("error"),
            }
            
        except Exception as e:
            logger.error(f"Error checking source {config.id}: {e}")
            freshness.consecutive_failures += 1
            freshness.consecutive_successes = 0
            freshness.total_checks += 1
            freshness.current_status = FreshnessStatus.ERROR
            freshness.failed_checks += 1
            
            return {
                "success": False,
                "status": FreshnessStatus.ERROR.value,
                "error": str(e),
            }

    async def _check_rss(self, config: SourceConfig) -> Dict[str, Any]:
        """Check RSS/Atom feed."""
        import feedparser
        
        try:
            headers = {"User-Agent": "ULTRON Freshness Tracker/1.0"}
            headers.update(config.headers)
            
            if config.last_etag:
                config.headers["If-None-Match"] = config.last_etag
            if config.last_modified:
                config.headers["If-Modified-Since"] = config.last_modified
            
            response = await asyncio.to_thread(
                requests.get, config.url, headers=config.headers, 
                timeout=config.timeout_seconds, verify=config.verify_ssl
            )
            
            if response.status_code == 304:
                return {"success": True, "items_count": 0, "content_hash": config.last_content_hash}
            
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            
            if feed.bozo and not feed.entries:
                return {"success": False, "error": "Feed parsing failed"}
            
            items_count = len(feed.entries)
            content_hash = hashlib.sha256(response.content).hexdigest()
            
            # Update cache headers
            config.last_etag = response.headers.get("ETag")
            config.last_modified = response.headers.get("Last-Modified")
            config.last_content_hash = content_hash
            
            return {
                "success": True,
                "items_count": items_count,
                "content_hash": content_hash,
            }
            
        except Exception as e:
            logger.error(f"RSS check failed for {config.id}: {e}")
            return {"success": False, "error": str(e)}

    async def _check_api(self, config: SourceConfig) -> Dict[str, Any]:
        """Check API endpoint."""
        try:
            headers = {"User-Agent": "ULTRON Freshness Tracker/1.0"}
            headers.update(config.headers)
            
            if config.auth:
                if config.auth.get("type") == "bearer":
                    headers["Authorization"] = f"Bearer {config.auth.get('token')}"
                elif config.auth.get("type") == "api_key":
                    headers[config.auth.get("header", "X-API-Key")] = config.auth.get("key")
            
            response = await asyncio.to_thread(
                requests.get, config.url, headers=headers, 
                timeout=config.timeout_seconds, verify=config.verify_ssl
            )
            
            if response.status_code == 304:
                return {"success": True, "items_count": 0}
            
            response.raise_for_status()
            data = response.json()
            
            # Try to count items
            items_count = 0
            if isinstance(data, list):
                items_count = len(data)
            elif isinstance(data, dict):
                for key in ["data", "results", "items", "articles", "entries"]:
                    if key in data and isinstance(data[key], list):
                        items_count = len(data[key])
                        break
            
            content_hash = hashlib.sha256(response.content).hexdigest()
            
            return {
                "success": True,
                "items_count": items_count,
                "content_hash": content_hash,
            }
            
        except Exception as e:
            logger.error(f"API check failed for {config.id}: {e}")
            return {"success": False, "error": str(e)}

    async def _check_html(self, config: SourceConfig) -> Dict[str, Any]:
        """Check HTML page for changes."""
        try:
            headers = {"User-Agent": "ULTRON Freshness Tracker/1.0"}
            headers.update(config.headers)
            
            response = await asyncio.to_thread(
                requests.get, config.url, headers=headers,
                timeout=config.timeout_seconds, verify=config.verify_ssl
            )
            response.raise_for_status()
            
            # Extract meaningful content (skip scripts, styles)
            soup = BeautifulSoup(response.content, 'html.parser')
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()
            
            content = soup.get_text()
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            
            return {
                "success": True,
                "items_count": 1,
                "content_hash": content_hash,
            }
            
        except Exception as e:
            logger.error(f"HTML check failed for {config.id}: {e}")
            return {"success": False, "error": str(e)}

    async def _check_generic(self, config: SourceConfig) -> Dict[str, Any]:
        """Generic check for unknown source types."""
        try:
            response = await asyncio.to_thread(
                requests.get, config.url, headers=config.headers,
                timeout=config.timeout_seconds, verify=config.verify_ssl
            )
            response.raise_for_status()
            
            content_hash = hashlib.sha256(response.content).hexdigest()
            return {
                "success": True,
                "items_count": 1,
                "content_hash": content_hash,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def check_all(self) -> Dict[str, Dict]:
        """Check all registered sources."""
        results = {}
        
        for source_id in self._sources:
            result = await self.check_source(source_id)
            results[source_id] = result
            # Small delay between checks
            await asyncio.sleep(0.5)
        
        return results

    def get_health_summary(self) -> Dict[str, Any]:
        """Get overall health summary."""
        total = len(self._sources)
        healthy = sum(1 for f in self._freshness.values() if f.current_status == FreshnessStatus.FRESH)
        stale = sum(1 for f in self._freshness.values() if f.current_status == FreshnessStatus.STALE)
        critical = sum(1 for f in self._freshness.values() if f.current_status == FreshnessStatus.STALE_CRITICAL)
        errors = sum(1 for f in self._freshness.values() if f.current_status == FreshnessStatus.ERROR)
        unknown = sum(1 for f in self._freshness.values() if f.current_status == FreshnessStatus.UNKNOWN)
        
        return {
            "total_sources": total,
            "healthy": healthy,
            "stale": stale,
            "critical": critical,
            "errors": errors,
            "unknown": unknown,
            "last_check": max([f.last_check for f in self._freshness.values() if f.last_check], default=None),
            "by_status": {
                "fresh": healthy,
                "stale": stale,
                "critical": critical,
                "error": errors,
                "unknown": unknown,
            }
        }

    async def start_monitoring(self, interval_minutes: int = 60):
        """Start periodic monitoring of all sources."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop(interval_minutes))
        logger.info("Freshness monitoring started")

    async def stop_monitoring(self):
        """Stop periodic monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self, interval_minutes: int):
        """Background monitoring loop."""
        while self._running:
            try:
                await self.check_all()
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}")
            
            await asyncio.sleep(interval_minutes * 60)

    def get_source_history(self, source_id: str, limit: int = 100) -> List[Dict]:
        """Get check history for a source."""
        freshness = self._freshness.get(source_id)
        if not freshness:
            return []
        
        return freshness.freshness_history[-limit:]

    def export_freshness_report(self, output_path: Path = None) -> str:
        """Export freshness report as JSON."""
        report = {
            "generated": datetime.now().isoformat(),
            "summary": self.get_health_summary(),
            "sources": {},
        }
        
        for source_id, freshness in self._freshness.items():
            config = self._sources.get(source_id)
            report[f"sources"][source_id] = {
                "config": {
                    "id": config.id,
                    "name": config.name,
                    "url": config.url,
                    "type": config.source_type.value,
                    "enabled": config.enabled,
                },
                "freshness": self.get_freshness(source_id),
                "history": freshness.freshness_history,
            }
        
        if output_path:
            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2, default=str)
            return str(output_path)
        else:
            return json.dumps(report, indent=2, default=str)


# Global instance
_source_freshness_tracker: Optional[SourceFreshnessTracker] = None


def get_source_freshness_tracker() -> SourceFreshnessTracker:
    """Get or create the global Source Freshness Tracker instance."""
    global _source_freshness_tracker
    if _source_freshness_tracker is None:
        _source_freshness_tracker = SourceFreshnessTracker()
    return _source_freshness_tracker