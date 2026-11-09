"""ULTRON Circuit Breaker and Retry Logic"""
import time
import threading
import random
from enum import Enum
from typing import Callable, Any, Optional, Type, Tuple, List, Dict
from dataclasses import dataclass, field
from functools import wraps
from collections import deque
from threading import Lock

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 2          # Successes in half-open before closing
    timeout: float = 30.0               # Seconds before half-open
    excluded_exceptions: Tuple[Type[Exception], ...] = ()  # Don't count these


@dataclass
class CircuitBreakerStats:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    state_changes: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None


class CircuitBreaker:
    """Circuit breaker implementation with thread safety"""

    def __init__(self, name: str, config: CircuitBreakerConfig = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_state_change = time.time()
        self._lock = Lock()
        self.stats = CircuitBreakerStats()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if timeout has passed
                if time.time() - self._last_state_change >= self.config.timeout:
                    self._transition_to_half_open()
            return self._state

    def _transition_to_open(self):
        self._state = CircuitState.OPEN
        self._last_state_change = time.time()
        self.stats.state_changes += 1
        audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                  details={"circuit": self.name, "action": "opened", "failures": self._failure_count})

    def _transition_to_half_open(self):
        self._state = CircuitState.HALF_OPEN
        self._success_count = 0
        self._last_state_change = time.time()
        self.stats.state_changes += 1
        audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                  details={"circuit": self.name, "action": "half_open"})

    def _transition_to_closed(self):
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_state_change = time.time()
        self.stats.state_changes += 1
        audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                  details={"circuit": self.name, "action": "closed"})

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        current_state = self.state

        if current_state == CircuitState.OPEN:
            self.stats.rejected_calls += 1
            raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN")

        self.stats.total_calls += 1

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.config.excluded_exceptions:
            raise
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        with self._lock:
            self.stats.successful_calls += 1
            self.stats.last_success_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._transition_to_closed()

    def _on_failure(self):
        with self._lock:
            self.stats.failed_calls += 1
            self.stats.last_failure_time = time.time()
            self._failure_count += 1

            if self._state == CircuitState.HALF_OPEN:
                self._transition_to_open()
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.config.failure_threshold:
                    self._transition_to_open()

    def reset(self):
        """Manually reset circuit breaker"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_state_change = time.time()

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                **self.stats.__dict__,
            }


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


# Global circuit breaker registry
_circuit_breakers: Dict[str, CircuitBreaker] = {}
_circuit_lock = Lock()


def get_circuit_breaker(name: str, config: CircuitBreakerConfig = None) -> CircuitBreaker:
    """Get or create circuit breaker by name"""
    global _circuit_breakers
    with _circuit_lock:
        if name not in _circuit_breakers:
            _circuit_breakers[name] = CircuitBreaker(name, config)
        return _circuit_breakers[name]


def circuit_breaker(name: str = None, config: CircuitBreakerConfig = None):
    """Decorator for circuit breaker protection"""
    def decorator(func: Callable) -> Callable:
        cb_name = name or f"{func.__module__}.{func.__qualname__}"
        cb = get_circuit_breaker(cb_name, config)

        @wraps(func)
        def wrapper(*args, **kwargs):
            return cb.call(func, *args, **kwargs)
        return wrapper
    return decorator


def circuit_breaker_async(name: str = None, config: CircuitBreakerConfig = None):
    """Async decorator for circuit breaker protection"""
    def decorator(func: Callable) -> Callable:
        cb_name = name or f"{func.__module__}.{func.__qualname__}"
        cb = get_circuit_breaker(cb_name, config)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            return cb.call(func, *args, **kwargs)
        return wrapper
    return decorator


# ========== Retry Logic ==========

@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0          # Initial delay in seconds
    max_delay: float = 60.0          # Maximum delay
    exponential_base: float = 2.0    # Exponential backoff base
    jitter: float = 0.1              # Jitter factor (0-1)
    retry_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    excluded_exceptions: Tuple[Type[Exception], ...] = ()  # Exceptions that should not be retried
    retry_on_result: Callable[[Any], bool] = None  # Custom retry condition
    timeout: float = None            # Total timeout for all attempts


@dataclass
class RetryStats:
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    total_time: float = 0.0


def retry(config: RetryConfig = None):
    """Decorator for retry logic with exponential backoff"""
    config = config or RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            stats = RetryStats()
            start_time = time.time()
            last_exception = None

            excluded_exception = None
            for attempt in range(1, config.max_attempts + 1):
                stats.total_attempts += 1

                try:
                    result = func(*args, **kwargs)

                    # Check custom retry condition
                    if config.retry_on_result and config.retry_on_result(result):
                        raise RetryableError(f"Result indicates retry needed: {result}")

                    stats.successful_attempts += 1
                    stats.total_time = time.time() - start_time
                    return result

                except config.retry_exceptions as e:
                    # Check if this exception should be excluded from retry
                    if config.excluded_exceptions and isinstance(e, config.excluded_exceptions):
                        excluded_exception = e
                        break
                    
                    last_exception = e
                    stats.failed_attempts += 1

                    if attempt == config.max_attempts:
                        stats.total_time = time.time() - start_time
                        raise RetryExhaustedError(
                            f"Retry exhausted after {config.max_attempts} attempts"
                        ) from e

                    # Calculate delay with exponential backoff and jitter
                    delay = min(
                        config.base_delay * (config.exponential_base ** (attempt - 1)),
                        config.max_delay
                    )
                    jitter = delay * config.jitter * random.uniform(-1, 1)
                    delay = max(0, delay + jitter)

                    # Check total timeout
                    if config.timeout and (time.time() - start_time + delay) > config.timeout:
                        stats.total_time = time.time() - start_time
                        raise RetryTimeoutError("Retry timeout exceeded") from e

                    time.sleep(delay)

            if excluded_exception is not None:
                raise excluded_exception

            # Should not reach here
            stats.total_time = time.time() - start_time
            raise last_exception

        return wrapper
    return decorator


def retry_async(config: RetryConfig = None):
    """Async version of retry decorator"""
    config = config or RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    result = await func(*args, **kwargs)

                    if config.retry_on_result and config.retry_on_result(result):
                        raise RetryableError(f"Result indicates retry needed")

                    return result

                except config.retry_exceptions as e:
                    if attempt == config.max_attempts:
                        raise RetryExhaustedError(
                            f"Retry exhausted after {config.max_attempts} attempts"
                        ) from e

                    delay = min(
                        config.base_delay * (config.exponential_base ** (attempt - 1)),
                        config.max_delay
                    )
                    jitter = delay * config.jitter * random.uniform(-1, 1)
                    delay = max(0, delay + jitter)

                    if config.timeout and (time.time() + delay) > config.timeout:
                        raise RetryTimeoutError("Retry timeout exceeded") from e

                    await asyncio.sleep(delay)

        return wrapper
    return decorator


class RetryableError(Exception):
    """Raised when result indicates retry should happen"""
    pass


class RetryExhaustedError(Exception):
    """Raised when all retry attempts exhausted"""
    pass


class RetryTimeoutError(Exception):
    """Raised when total retry timeout exceeded"""
    pass


# Global retry stats collector
_retry_stats: Dict[str, RetryStats] = {}


def get_retry_stats(name: str) -> Optional[RetryStats]:
    """Get retry statistics for a function"""
    return _retry_stats.get(name)


def clear_retry_stats(name: str = None):
    """Clear retry statistics"""
    global _retry_stats
    if name:
        _retry_stats.pop(name, None)
    else:
        _retry_stats.clear()


# Import asyncio for async retry
try:
    import asyncio
except ImportError:
    asyncio = None