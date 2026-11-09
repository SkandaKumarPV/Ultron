"""ULTRON Metrics and Observability"""
import time
import threading
import json
import statistics
from collections import defaultdict, deque
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from functools import wraps
from datetime import datetime
from contextlib import contextmanager

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


@dataclass
class MetricValue:
    """Single metric measurement"""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)
    metric_type: str = "gauge"  # gauge, counter, histogram, summary


@dataclass
class HistogramBucket:
    """Histogram bucket for latency tracking"""
    le: float  # Upper bound
    count: int = 0


class MetricsCollector:
    """Thread-safe metrics collector with multiple metric types"""

    def __init__(self):
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._summaries: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._lock = threading.RLock()
        self._start_time = time.time()

    # Counter operations
    def increment(self, name: str, value: float = 1.0, labels: Dict[str, str] = None):
        """Increment counter"""
        key = self._make_key(name, labels)
        with self._lock:
            self._counters[key] += value

    def decrement(self, name: str, value: float = 1.0, labels: Dict[str, str] = None):
        """Decrement counter"""
        self.increment(name, -value, labels)

    # Gauge operations
    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set gauge value"""
        key = self._make_key(name, labels)
        with self._lock:
            self._gauges[key] = value

    # Histogram operations
    def observe(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record histogram observation"""
        key = self._make_key(name, labels)
        with self._lock:
            self._histograms[key].append(value)

    def observe_latency(self, name: str, duration: float, labels: Dict[str, str] = None):
        """Record latency in milliseconds"""
        self.observe(f"{name}_latency_ms", duration * 1000, labels)

    # Summary operations
    def record(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record value for summary statistics"""
        key = self._make_key(name, labels)
        with self._lock:
            self._summaries[key].append(value)

    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """Create metric key with labels"""
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    # Query operations
    def get_counter(self, name: str, labels: Dict[str, str] = None) -> float:
        key = self._make_key(name, labels)
        with self._lock:
            return self._counters.get(key, 0.0)

    def get_gauge(self, name: str, labels: Dict[str, str] = None) -> Optional[float]:
        key = self._make_key(name, labels)
        with self._lock:
            return self._gauges.get(key)

    def get_histogram_stats(self, name: str, labels: Dict[str, str] = None) -> Dict[str, float]:
        key = self._make_key(name, labels)
        with self._lock:
            values = self._histograms.get(key, [])
            if not values:
                return {}
            return {
                "count": len(values),
                "sum": sum(values),
                "min": min(values),
                "max": max(values),
                "mean": statistics.mean(values),
                "median": statistics.median(values) if len(values) > 1 else values[0],
                "p50": self._percentile(values, 50),
                "p90": self._percentile(values, 90),
                "p95": self._percentile(values, 95),
                "p99": self._percentile(values, 99),
            }

    def get_summary_stats(self, name: str, labels: Dict[str, str] = None) -> Dict[str, float]:
        key = self._make_key(name, labels)
        with self._lock:
            values = list(self._summaries.get(key, []))
            if not values:
                return {}
            return {
                "count": len(values),
                "sum": sum(values),
                "min": min(values),
                "max": max(values),
                "mean": statistics.mean(values),
                "median": statistics.median(values) if len(values) > 1 else values[0],
                "p50": self._percentile(values, 50),
                "p90": self._percentile(values, 90),
                "p95": self._percentile(values, 95),
                "p99": self._percentile(values, 99),
            }

    def _percentile(self, values: List[float], percentile: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        index = (percentile / 100) * (len(sorted_vals) - 1)
        if index.is_integer():
            return sorted_vals[int(index)]
        lower = sorted_vals[int(index)]
        upper = sorted_vals[int(index) + 1]
        return lower + (upper - lower) * (index - int(index))

    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all metrics as dictionary"""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: self.get_histogram_stats(k.replace("{", "").replace("}", "").split("{")[0]) 
                               for k in self._histograms.keys()},
                "summaries": {k: self.get_summary_stats(k.replace("{", "").replace("}", "").split("{")[0]) 
                              for k in self._summaries.keys()},
                "uptime_seconds": time.time() - self._start_time,
            }

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format"""
        lines = []
        with self._lock:
            # Counters
            for key, value in self._counters.items():
                name, labels = self._parse_key(key)
                label_str = self._format_labels(labels)
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{label_str} {value}")

            # Gauges
            for key, value in self._gauges.items():
                name, labels = self._parse_key(key)
                label_str = self._format_labels(labels)
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name}{label_str} {value}")

            # Histograms
            for key, values in self._histograms.items():
                if not values:
                    continue
                name, labels = self._parse_key(key)
                label_str = self._format_labels(labels)
                count = len(values)
                sum_val = sum(values)
                lines.append(f"# TYPE {name} histogram")
                lines.append(f"{name}_count{label_str} {count}")
                lines.append(f"{name}_sum{label_str} {sum_val}")

        return "\n".join(lines) + "\n"

    def _parse_key(self, key: str) -> tuple:
        """Parse metric key into name and labels"""
        if "{" in key:
            name, label_part = key.split("{", 1)
            label_part = label_part.rstrip("}")
            labels = {}
            for part in label_part.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    labels[k] = v.strip('"')
        else:
            name = key
            labels = {}
        return name, labels

    def _format_labels(self, labels: Dict[str, str]) -> str:
        if not labels:
            return ""
        return "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"


class Timer:
    """Context manager for timing operations"""

    def __init__(self, metrics: 'MetricsCollector', name: str, labels: Dict[str, str] = None):
        self.metrics = metrics
        self.name = name
        self.labels = labels
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        self.metrics.observe_latency(self.name, duration, self.labels)


def timed(metrics: 'MetricsCollector', name: str, labels: Dict[str, str] = None):
    """Decorator for timing function execution"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            with Timer(metrics, name, labels):
                return func(*args, **kwargs)
        return wrapper
    return decorator


class HealthCheck:
    """Health check framework"""

    def __init__(self):
        self._checks: Dict[str, Callable[[], bool]] = {}
        self._lock = threading.RLock()

    def register(self, name: str, check: Callable[[], bool], timeout: float = 5.0):
        """Register a health check"""
        with self._lock:
            self._checks[name] = (check, timeout)

    def unregister(self, name: str):
        with self._lock:
            self._checks.pop(name, None)

    def run_check(self, name: str) -> Dict[str, Any]:
        """Run a specific health check"""
        with self._lock:
            check_info = self._checks.get(name)
            if not check_info:
                return {"name": name, "status": "unknown", "error": "Check not registered"}

        check_func, timeout = check_info
        start = time.time()
        try:
            # Run with timeout
            import signal
            def timeout_handler(signum, frame):
                raise TimeoutError(f"Health check {name} timed out")
            
            # Simple timeout using threading
            result = [None]
            exception = [None]
            
            def target():
                try:
                    result[0] = check_func()
                except Exception as e:
                    exception[0] = e
            
            thread = threading.Thread(target=target)
            thread.start()
            thread.join(timeout=timeout)
            
            if thread.is_alive():
                return {
                    "name": name,
                    "status": "timeout",
                    "duration_ms": (time.time() - start) * 1000,
                    "error": f"Timeout after {timeout}s"
                }
            
            if exception[0]:
                return {
                    "name": name,
                    "status": "unhealthy",
                    "duration_ms": (time.time() - start) * 1000,
                    "error": str(exception[0])
                }

            duration = (time.time() - start) * 1000
            return {
                "name": name,
                "status": "healthy" if result[0] else "unhealthy",
                "duration_ms": duration,
            }

        except Exception as e:
            return {
                "name": name,
                "status": "error",
                "duration_ms": (time.time() - start) * 1000,
                "error": str(e)
            }

    def run_all(self) -> Dict[str, Any]:
        """Run all health checks"""
        with self._lock:
            names = list(self._checks.keys())

        results = {}
        overall = "healthy"
        for name in names:
            result = self.run_check(name)
            results[name] = result
            if result["status"] != "healthy":
                overall = "unhealthy"

        return {
            "status": overall,
            "timestamp": datetime.now().isoformat(),
            "checks": results,
        }


# Global metrics instance
_metrics = MetricsCollector()
_health_checks = HealthCheck()


def get_metrics() -> MetricsCollector:
    return _metrics


def get_health_checks() -> HealthCheck:
    return _health_checks


# Convenience functions
def increment_counter(name: str, value: float = 1.0, labels: Dict[str, str] = None):
    _metrics.increment(name, value, labels)


def set_gauge(name: str, value: float, labels: Dict[str, str] = None):
    _metrics.set_gauge(name, value, labels)


def observe_histogram(name: str, value: float, labels: Dict[str, str] = None):
    _metrics.observe(name, value, labels)


def observe_latency(name: str, duration: float, labels: Dict[str, str] = None):
    _metrics.observe_latency(name, duration, labels)


def record_summary(name: str, value: float, labels: Dict[str, str] = None):
    _metrics.record(name, value, labels)


def get_metrics_snapshot() -> Dict[str, Any]:
    return _metrics.get_all_metrics()


def export_prometheus() -> str:
    return _metrics.export_prometheus()


def time_operation(metrics: MetricsCollector, name: str, labels: Dict[str, str] = None):
    """Context manager for timing operations"""
    return Timer(metrics, name, labels)


def timed(metrics: MetricsCollector, name: str, labels: Dict[str, str] = None):
    """Decorator for timing function execution"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            with Timer(metrics, name, labels):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def health_check(name: str = None, timeout: float = 5.0):
    """Decorator for registering health checks"""
    def decorator(func: Callable[[], bool]) -> Callable[[], bool]:
        check_name = name or func.__name__
        _health_checks.register(check_name, func, timeout)
        return func
    return decorator


def run_health_checks() -> Dict[str, Any]:
    return _health_checks.run_all()