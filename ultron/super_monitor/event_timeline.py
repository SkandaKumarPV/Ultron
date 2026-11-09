"""
ULTRON Super Monitor - Event Timeline
Creates and manages event timelines with evidence linking.
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
import uuid

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class TimelineEventType(Enum):
    """Types of timeline events."""
    NEWS = "news"
    WEATHER = "weather"
    DISASTER = "disaster"
    EVIDENCE = "evidence"
    NOTE = "note"
    ALERT = "alert"
    SYSTEM = "system"


@dataclass
class TimelineEvent:
    """An event on a timeline."""
    id: str
    type: TimelineEventType
    title: str
    description: str
    timestamp: datetime
    source: str
    url: Optional[str] = None
    location: Optional[str] = None
    coordinates: Optional[tuple] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    related_event_ids: List[str] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class Timeline:
    """A timeline of related events."""
    id: str
    title: str
    description: str
    created: datetime
    updated: datetime
    events: List[str] = field(default_factory=list)  # Event IDs
    metadata: Dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


class EventTimeline:
    """
    Manages event timelines with evidence linking.
    Supports multiple timelines, event linking, and evidence attachment.
    """

    def __init__(self, max_events: int = 10000):
        self.max_events = max_events
        self._events: Dict[str, TimelineEvent] = {}  # id -> TimelineEvent
        self._timelines: Dict[str, Timeline] = {}
        self._event_index: Dict[str, List[str]] = {
            "type": {},  # type -> event_ids
            "source": {},  # source -> event_ids
            "location": {},  # location -> event_ids
            "tag": {},  # tag -> event_ids
        }
        self._lock = asyncio.Lock()

    async def add_event(self, event: TimelineEvent) -> str:
        """Add an event to the timeline."""
        async with self._lock:
            if len(self._events) >= self.max_events:
                # Remove oldest event
                oldest_id = min(self._events.keys(), 
                               key=lambda k: self._events[k].timestamp)
                self._remove_event(oldest_id)
            
            self._events[event.id] = event
            self._update_indexes(event)
            
            # Add to default timeline if exists
            if "default" in self._timelines:
                self._timelines["default"].events.append(event.id)
                self._timelines["default"].updated = datetime.now()
            
            return event.id

    async def get_event(self, event_id: str) -> Optional[TimelineEvent]:
        """Get event by ID."""
        async with self._lock:
            return self._events.get(event_id)

    async def update_event(self, event_id: str, **kwargs) -> bool:
        """Update event fields."""
        async with self._lock:
            event = self._events.get(event_id)
            if not event:
                return False
            
            for key, value in kwargs.items():
                if hasattr(event, key):
                    setattr(event, key, value)
            
            event.metadata.update(kwargs.get("metadata", {}))
            
            # Rebuild indexes if needed
            self._rebuild_indexes()
            
            return True

    async def delete_event(self, event_id: str) -> bool:
        """Delete an event."""
        async with self._lock:
            return self._remove_event(event_id)

    async def get_events(self,
                         event_type: Optional[str] = None,
                         source: Optional[str] = None,
                         location: Optional[str] = None,
                         tags: Optional[List[str]] = None,
                         start_time: Optional[datetime] = None,
                         end_time: Optional[datetime] = None,
                         limit: int = 100) -> List[TimelineEvent]:
        """Query events with filters."""
        async with self._lock:
            events = list(self._events.values())
            
            if event_type:
                events = [e for e in events if e.type.value == event_type]
            if source:
                events = [e for e in events if e.source == source]
            if location:
                events = [e for e in events if e.location and location.lower() in e.location.lower()]
            if tags:
                events = [e for e in events if all(t in e.tags for t in tags)]
            if start_time:
                events = [e for e in events if e.timestamp >= start_time]
            if end_time:
                events = [e for e in events if e.timestamp <= end_time]
            
            # Sort by timestamp descending
            events.sort(key=lambda e: e.timestamp, reverse=True)
            
            return events[:limit]

    async def link_events(self, event_id_1: str, event_id_2: str, 
                          relationship: str = "related") -> bool:
        """Link two events together."""
        async with self._lock:
            event1 = self._events.get(event_id_1)
            event2 = self._events.get(event_id_2)
            
            if not event1 or not event2:
                return False
            
            if event_id_2 not in event1.related_event_ids:
                event1.related_event_ids.append(event_id_2)
            if event_id_1 not in event2.related_event_ids:
                event2.related_event_ids.append(event_id_1)
            
            return True

    async def add_evidence(self, event_id: str, evidence_id: str) -> bool:
        """Link evidence to an event."""
        async with self._lock:
            event = self._events.get(event_id)
            if not event:
                return False
            
            if evidence_id not in event.evidence_ids:
                event.evidence_ids.append(evidence_id)
                return True
            return False

    async def create_timeline(self, 
                              title: str,
                              description: str,
                              event_ids: Optional[List[str]] = None,
                              tags: Optional[List[str]] = None) -> str:
        """Create a new timeline."""
        timeline_id = str(uuid.uuid4())[:8]
        
        timeline = Timeline(
            id=timeline_id,
            title=title,
            description=description,
            created=datetime.now(),
            updated=datetime.now(),
            events=event_ids or [],
            tags=tags or [],
        )
        
        self._timelines[timeline_id] = timeline
        return timeline_id

    async def get_timeline(self, timeline_id: str) -> Optional[Dict]:
        """Get timeline with events."""
        timeline = self._timelines.get(timeline_id)
        if not timeline:
            return None
        
        events = []
        for eid in timeline.events:
            event = self._events.get(eid)
            if event:
                events.append({
                    "id": event.id,
                    "type": event.type.value,
                    "title": event.title,
                    "description": event.description,
                    "timestamp": event.timestamp.isoformat(),
                    "source": event.source,
                    "location": event.location,
                    "tags": event.tags,
                })
        
        # Sort by timestamp
        events.sort(key=lambda e: e["timestamp"])
        
        return {
            "id": timeline.id,
            "title": timeline.title,
            "description": timeline.description,
            "created": timeline.created.isoformat(),
            "updated": timeline.updated.isoformat(),
            "events": events,
            "tags": timeline.tags,
            "metadata": timeline.metadata,
        }

    async def update_timeline(self, timeline_id: str, **kwargs) -> bool:
        """Update timeline metadata."""
        timeline = self._timelines.get(timeline_id)
        if not timeline:
            return False
        
        for key, value in kwargs.items():
            if hasattr(timeline, key):
                setattr(timeline, key, value)
        
        timeline.updated = datetime.now()
        return True

    async def add_event_to_timeline(self, timeline_id: str, event_id: str) -> bool:
        """Add an event to a timeline."""
        timeline = self._timelines.get(timeline_id)
        event = self._events.get(event_id)
        
        if not timeline or not event:
            return False
        
        if event_id not in timeline.events:
            timeline.events.append(event_id)
            timeline.updated = datetime.now()
            return True
        return False

    async def remove_event_from_timeline(self, timeline_id: str, event_id: str) -> bool:
        """Remove event from timeline."""
        timeline = self._timelines.get(timeline_id)
        if not timeline:
            return False
        
        if event_id in timeline.events:
            timeline.events.remove(event_id)
            timeline.updated = datetime.now()
            return True
        return False

    def _update_indexes(self, event: TimelineEvent):
        """Update search indexes for event."""
        # By type
        self._event_index.setdefault("type", {}).setdefault(event.type.value, []).append(event.id)
        
        # By source
        self._event_index.setdefault("source", {}).setdefault(event.source, []).append(event.id)
        
        # By location
        if event.location:
            self._event_index.setdefault("location", {}).setdefault(event.location.lower(), []).append(event.id)
        
        # By tags
        for tag in event.tags:
            self._event_index.setdefault("tag", {}).setdefault(tag.lower(), []).append(event.id)

    def _remove_event(self, event_id: str):
        """Remove event from all indexes."""
        event = self._events.pop(event_id, None)
        if not event:
            return
        
        # Remove from indexes
        for index_name, index in self._event_index.items():
            for key, ids in index.items():
                if event_id in ids:
                    ids.remove(event_id)
                    if not ids:
                        del index[key]
        
        # Remove from timelines
        for timeline in self._timelines.values():
            if event_id in timeline.events:
                timeline.events.remove(event_id)

    def _rebuild_indexes(self):
        """Rebuild all indexes from events."""
        self._event_index = {
            "type": {},
            "source": {},
            "location": {},
            "tag": {},
        }
        
        for event in self._events.values():
            self._update_indexes(event)

    def get_related_events(self, event_id: str, max_depth: int = 2) -> List[TimelineEvent]:
        """Get related events up to max_depth."""
        visited = set()
        result = []
        
        def traverse(eid: str, depth: int):
            if depth > max_depth or eid in visited:
                return
            visited.add(eid)
            
            event = self._events.get(eid)
            if not event:
                return
            
            result.append(event)
            
            for rid in event.related_event_ids:
                traverse(rid, depth + 1)
        
        traverse(event_id, 0)
        return result

    def get_timeline_stats(self) -> Dict[str, Any]:
        """Get timeline statistics."""
        return {
            "total_events": len(self._events),
            "total_timelines": len(self._timelines),
            "events_by_type": {
                t.value: len(ids) for t, ids in self._event_index.get("type", {}).items()
            },
            "events_by_source": {
                s: len(ids) for s, ids in self._event_index.get("source", {}).items()
            },
            "events_by_location": {
                l: len(ids) for l, ids in self._event_index.get("location", {}).items()
            },
            "timeline_count": len(self._timelines),
        }

    async def export_timeline(self, timeline_id: str, format: str = "json") -> Optional[str]:
        """Export timeline in specified format."""
        timeline_data = await self.get_timeline(timeline_id)
        if not timeline_data:
            return None
        
        if format == "json":
            return json.dumps(timeline_data, indent=2, default=str)
        elif format == "jsonl":
            lines = [json.dumps(e, default=str) for e in timeline_data["events"]]
            return "\n".join(lines)
        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            if timeline_data["events"]:
                writer = csv.DictWriter(output, fieldnames=timeline_data["events"][0].keys())
                writer.writeheader()
                writer.writerows(timeline_data["events"])
            return output.getvalue()
        
        return None

    def _remove_event(self, event_id: str) -> bool:
        """Internal remove event."""
        event = self._events.pop(event_id, None)
        if not event:
            return False
        
        self._remove_event(event_id)
        return True


# Global instance
_event_timeline: Optional[EventTimeline] = None


def get_event_timeline() -> EventTimeline:
    """Get or create the global Event Timeline instance."""
    global _event_timeline
    if _event_timeline is None:
        _event_timeline = EventTimeline()
    return _event_timeline