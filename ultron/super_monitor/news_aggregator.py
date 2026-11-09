"""
ULTRON Super Monitor - News Aggregator
Aggregates news from RSS feeds and news APIs.
"""

import asyncio
import logging
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from pathlib import Path
import asyncio

import feedparser
import requests
from bs4 import BeautifulSoup

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


@dataclass
class NewsSource:
    """Configuration for a news source."""
    url: str
    name: str
    type: str = "rss"  # rss, api, html
    category: str = "general"
    enabled: bool = True
    max_items: int = 50
    headers: Dict[str, str] = field(default_factory=dict)
    last_fetch: Optional[datetime] = None
    last_etag: Optional[str] = None
    last_modified: Optional[str] = None


@dataclass
class NewsEvent:
    """A news event/article."""
    id: str
    source: str
    title: str
    summary: str
    url: str
    published: datetime
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    hash: str = ""  # For deduplication


class NewsAggregator:
    """
    Aggregates news from multiple RSS feeds and news sources.
    Supports RSS/Atom feeds, HTML scraping, and API endpoints.
    """

    def __init__(self, 
                 sources: List[str],
                 max_events_per_source: int = 100,
                 cache_dir: Optional[Path] = None):
        self.sources: List[NewsSource] = []
        self.max_events_per_source = max_events_per_source
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._events_buffer: List[Dict] = []
        self._seen_hashes: Set[str] = set()
        
        # Initialize sources from URL list
        for url in sources:
            self.add_source(url)
        
        # Cache directory
        self.cache_dir = cache_dir or config.resolve_path("news_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self._running = False

    def add_source(self, url: str, name: Optional[str] = None, 
                   source_type: str = "rss", **kwargs) -> NewsSource:
        """Add a news source."""
        if name is None:
            name = url
        
        source = NewsSource(
            url=url,
            name=name,
            type=source_type,
            **kwargs
        )
        self.sources.append(source)
        return source

    async def start(self):
        """Start the news aggregator."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("News Aggregator started")

    async def stop(self):
        """Stop the news aggregator."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        """Main fetch loop."""
        while True:
            if not self._running:
                break
            
            try:
                await self.fetch_all()
            except Exception as e:
                logger.error(f"News fetch error: {e}")
            
            # Wait before next fetch (default 15 minutes)
            await asyncio.sleep(900)

    async def fetch_all(self) -> List[NewsEvent]:
        """Fetch news from all enabled sources."""
        all_events = []
        
        # Fetch from all sources concurrently
        tasks = []
        for source in self.sources:
            if source.enabled:
                tasks.append(self._fetch_source(source))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching from {self.sources[i].name}: {result}")
            elif isinstance(result, list):
                all_events.extend(result)
        
        # Deduplicate
        unique_events = self._deduplicate(all_events)
        
        # Update event buffer
        self._events_buffer.extend(unique_events)
        # Keep buffer size manageable
        if len(self._events_buffer) > 10000:
            self._events_buffer = self._events_buffer[-5000:]
        
        return unique_events

    async def _fetch_source(self, source: NewsSource) -> List[NewsEvent]:
        """Fetch news from a single source."""
        try:
            if source.type == "rss":
                return await self._fetch_rss(source)
            elif source.type == "api":
                return await self._fetch_api(source)
            elif source.type == "html":
                return await self._fetch_html(source)
            else:
                logger.warning(f"Unknown source type: {source.type}")
                return []
        except Exception as e:
            logger.error(f"Error fetching from {source.name}: {e}")
            return []

    async def _fetch_rss(self, source: NewsSource) -> List[NewsEvent]:
        """Fetch and parse RSS/Atom feed."""
        events = []
        
        try:
            headers = {"User-Agent": "ULTRON News Aggregator/1.0"}
            headers.update(source.headers)
            
            # Conditional request headers
            headers_list = headers
            if source.last_etag:
                headers_list["If-None-Match"] = source.last_etag
            if source.last_modified:
                headers_list["If-Modified-Since"] = source.last_modified
            
            # Fetch with timeout
            response = await asyncio.to_thread(
                requests.get, source.url, headers=headers_list, timeout=30
            )
            
            # Check if not modified
            if response.status_code == 304:
                logger.debug(f"No new content from {source.name}")
                return []
            
            response.raise_for_status()
            
            # Update cache headers
            source.last_etag = response.headers.get("ETag")
            source.last_modified = response.headers.get("Last-Modified")
            source.last_fetch = datetime.now()
            
            # Parse feed
            feed = feedparser.parse(response.content)
            
            if feed.bozo and not feed.entries:
                logger.warning(f"Feed parsing error for {source.name}: {feed.bozo_exception}")
            
            source_module = feedparser
            for entry in feed.entries[:self.max_events_per_source]:
                event = self._parse_entry(entry, source)
                if event:
                    events.append(event)
            
            return events
            
        except Exception as e:
            logger.error(f"Error fetching RSS {source.name}: {e}")
            return []

    async def _fetch_api(self, source: NewsSource) -> List[NewsEvent]:
        """Fetch from JSON API endpoint."""
        # Implementation for API endpoints
        # Placeholder for future API integrations
        return []

    async def _fetch_html(self, source: NewsSource) -> List[NewsEvent]:
        """Fetch and parse HTML page for news."""
        # Placeholder for HTML scraping
        return []

    def _parse_entry(self, entry: Any, source: NewsSource) -> Optional[NewsEvent]:
        """Parse a feed entry into a NewsEvent."""
        try:
            title = getattr(entry, 'title', '').strip()
            summary = getattr(entry, 'summary', getattr(entry, 'description', '')).strip()
            link = getattr(entry, 'link', '').strip()
            
            # Parse publication date
            published = datetime.now()
            for date_attr in ['published_parsed', 'updated_parsed', 'created_parsed']:
                if hasattr(entry, date_attr) and getattr(entry, date_attr):
                    try:
                        published = datetime(*getattr(entry, date_attr)[:6])
                        break
                    except Exception:
                        pass
            
            # Generate content hash for deduplication
            content = f"{title}{link}{summary}"
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            
            # Extract tags
            tags = []
            if hasattr(entry, 'tags'):
                tags = [tag.term for tag in entry.tags if hasattr(tag, 'term')]
            
            return NewsEvent(
                id=content_hash,
                source=source.name,
                title=title,
                summary=summary[:500],
                url=link,
                published=published,
                category=source.category,
                tags=tags,
                metadata={
                    "source_url": source.url,
                    "source_type": source.type,
                },
                hash=content_hash
            )
        except Exception as e:
            logger.warning(f"Failed to parse entry: {e}")
            return None

    def _deduplicate(self, events: List[NewsEvent]) -> List[NewsEvent]:
        """Remove duplicate events based on content hash."""
        unique = []
        for event in events:
            if event.hash not in self._seen_hashes:
                self._seen_hashes.add(event.hash)
                unique.append(event)
        return unique

    def query(self, query: str, limit: int = 10) -> List[Dict]:
        """Search news events by query."""
        query_lower = query.lower()
        results = []
        
        for event in self._events_buffer:
            text = f"{event.title} {event.summary} {' '.join(event.tags)}".lower()
            if query_lower in text:
                results.append({
                    "id": event.id,
                    "source": event.source,
                    "title": event.title,
                    "summary": event.summary,
                    "url": event.url,
                    "published": event.published.isoformat(),
                    "category": event.category,
                    "tags": event.tags,
                    "metadata": event.metadata,
                })
                if len(results) >= limit:
                    break
        
        return results

    def get_events(self, 
                   source: Optional[str] = None,
                   category: Optional[str] = None,
                   limit: int = 100) -> List[Dict]:
        """Get events with optional filters."""
        events = self._events_buffer.copy()
        
        if source:
            events = [e for e in events if e.source == source]
        if category:
            events = [e for e in events if e.category == category]
        
        # Sort by published date descending
        events.sort(key=lambda e: e.published, reverse=True)
        
        return [
            {
                "id": e.id,
                "source": e.source,
                "title": e.title,
                "summary": e.summary,
                "url": e.url,
                "published": e.published.isoformat(),
                "category": e.category,
                "tags": e.tags,
                "metadata": e.metadata,
            }
            for e in events[:limit]
        ]

    def get_status(self) -> Dict[str, Any]:
        """Get aggregator status."""
        return {
            "running": self._running,
            "sources_count": len([s for s in self.sources if s.enabled]),
            "total_sources": len(self.sources),
            "events_buffered": len(self._events_buffer),
            "unique_hashes": len(self._seen_hashes),
            "last_fetch": max([s.last_fetch for s in self.sources if s.last_fetch], default=None)
        }