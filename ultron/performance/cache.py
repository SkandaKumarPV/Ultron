"""ULTRON Performance Cache - LRU cache with TTL and persistence"""
import time
import threading
import pickle
import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Dict, Callable, Set, List
from functools import wraps
from collections import OrderedDict
from dataclasses import dataclass, field

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


@dataclass
class CacheEntry:
    """Cache entry with metadata"""
    value: Any
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: Optional[float] = None
    tags: Set[str] = field(default_factory=set)

    def is_expired(self) -> bool:
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl

    def touch(self):
        self.accessed_at = time.time()
        self.access_count += 1


class LRUCache:
    """Thread-safe LRU cache with TTL support"""

    def __init__(self, max_size: int = 1000, default_ttl: Optional[float] = None):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = {"hits": 0, "misses": 0, "evictions": 0, "expirations": 0}

    def _make_key(self, key: Any) -> str:
        """Normalize key to string"""
        if isinstance(key, str):
            return key
        return hashlib.sha256(pickle.dumps(key)).hexdigest()[:32]

    def get(self, key: Any, default: Any = None) -> Any:
        """Get value from cache"""
        key_str = self._make_key(key)
        
        with self._lock:
            entry = self._cache.get(key_str)
            if entry is None:
                self._stats["misses"] += 1
                return default

            if entry.is_expired():
                del self._cache[key_str]
                self._stats["misses"] += 1
                self._stats["expirations"] += 1
                return default

            entry.touch()
            # Move to end (most recently used)
            self._cache.move_to_end(key_str)
            self._stats["hits"] += 1
            return entry.value

    def set(self, key: Any, value: Any, ttl: Optional[float] = None, tags: Set[str] = None) -> bool:
        """Set value in cache"""
        key_str = self._make_key(key)
        ttl = ttl if ttl is not None else self.default_ttl

        with self._lock:
            # Evict if at capacity
            while len(self._cache) >= self.max_size:
                self._evict_lru()

            entry = CacheEntry(
                value=value,
                ttl=ttl,
                tags=tags or set(),
            )
            self._cache[key_str] = entry
            return True

    def delete(self, key: Any) -> bool:
        """Delete key from cache"""
        key_str = self._make_key(key)
        
        with self._lock:
            if key_str in self._cache:
                del self._cache[key_str]
                return True
            return False

    def clear(self, tags: Set[str] = None):
        """Clear cache, optionally by tags"""
        with self._lock:
            if tags is None:
                self._cache.clear()
            else:
                keys_to_delete = [
                    k for k, v in self._cache.items()
                    if v.tags & tags
                ]
                for k in keys_to_delete:
                    del self._cache[k]

    def _evict_lru(self):
        """Evict least recently used entry"""
        if self._cache:
            self._cache.popitem(last=False)
            self._stats["evictions"] += 1

    def cleanup_expired(self) -> int:
        """Remove all expired entries"""
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items()
                if v.is_expired()
            ]
            for k in expired_keys:
                del self._cache[k]
                self._stats["expirations"] += 1
            return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = self._stats["hits"] / total if total > 0 else 0
            return {
                **self._stats,
                "size": len(self._cache),
                "max_size": self.max_size,
                "hit_rate": hit_rate,
            }

    def get_keys(self, tags: Set[str] = None) -> List[str]:
        """Get all keys, optionally filtered by tags"""
        with self._lock:
            if tags is None:
                return list(self._cache.keys())
            return [
                k for k, v in self._cache.items()
                if v.tags & tags
            ]


class FunctionCache:
    """Decorator for caching function results"""

    def __init__(self, cache: LRUCache = None, key_func: Callable = None):
        self.cache = cache or LRUCache()
        self.key_func = key_func or (lambda *args, **kwargs: (args, tuple(sorted(kwargs.items()))))

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = self.key_func(*args, **kwargs)
            cached = self.cache.get(key)
            if cached is not None:
                return cached

            result = func(*args, **kwargs)
            self.cache.set(key, result)
            return result

        return wrapper

    def invalidate(self, *args, **kwargs):
        """Invalidate cache for specific arguments"""
        key = self.key_func(*args, **kwargs)
        self.cache.delete(key)

    def clear(self):
        self.cache.clear()


# Global cache instances
_default_cache = LRUCache(max_size=1000, default_ttl=3600)  # 1 hour default
_model_cache = LRUCache(max_size=100, default_ttl=1800)  # 30 min for model outputs
_embedding_cache = LRUCache(max_size=500, default_ttl=7200)  # 2 hours for embeddings


def get_cache(name: str = "default") -> LRUCache:
    """Get named cache instance"""
    caches = {
        "default": _default_cache,
        "model": _model_cache,
        "embedding": _embedding_cache,
    }
    return caches.get(name, _default_cache)


def cached(cache_name: str = "default", ttl: float = None, key_func: Callable = None):
    """Decorator for caching function results"""
    cache = get_cache(cache_name)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if key_func:
                key = key_func(*args, **kwargs)
            else:
                key = (func.__module__, func.__qualname__, args, tuple(sorted(kwargs.items())))

            cached = cache.get(key)
            if cached is not None:
                return cached

            result = func(*args, **kwargs)
            cache.set(key, result, ttl=ttl)
            return result

        return wrapper
    return decorator


def invalidate_cache(cache_name: str = "default", *args, **kwargs):
    """Invalidate cache for specific arguments"""
    cache = get_cache(cache_name)
    if not args and not kwargs:
        cache.clear()
    else:
        key = (args, tuple(sorted(kwargs.items())))
        cache.delete(key)


# Import Set for type hints
from typing import Set, List