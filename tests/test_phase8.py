"""ULTRON Phase 8 Tests - Production Hardening"""
import sys
from pathlib import Path
import time
import random

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultron.config import config
from ultron.performance.cache import LRUCache, get_cache, cached, invalidate_cache
from typing import Callable
from ultron.performance.resilience import CircuitBreaker, CircuitBreakerConfig, RetryConfig, retry, retry_async, get_circuit_breaker, circuit_breaker
from ultron.performance.metrics import MetricsCollector, Timer, get_metrics, health_check, run_health_checks, HealthCheck
from ultron.logging.structured import get_logger, initialize_logging, setup_logging
from ultron.performance.resilience import CircuitBreakerOpenError, RetryExhaustedError


def test_cache():
    """Test LRU cache"""
    print("Testing LRU Cache...")

    # Basic operations
    cache = LRUCache(max_size=10, default_ttl=1)
    cache.set("key1", "value1")
    assert cache.get("key1") == "value1"
    assert cache.get("missing") is None
    print("  [OK] Basic get/set")

    # TTL expiration
    cache.set("ttl_key", "ttl_value", ttl=0.1)
    assert cache.get("ttl_key") == "ttl_value"
    time.sleep(0.2)
    assert cache.get("ttl_key") is None
    print("  [OK] TTL expiration")

    # LRU eviction
    for i in range(15):
        cache.set(f"key{i}", f"value{i}")
    assert len(cache._cache) == 10  # max_size=10
    assert cache.get("key0") is None  # evicted
    print("  [OK] LRU eviction")

    # Stats
    stats = cache.get_stats()
    assert stats["hits"] >= 0
    assert stats["misses"] >= 0
    print("  [OK] Statistics")

    # Function cache
    call_count = 0

    @cached(ttl=1)
    def expensive_func(x):
        nonlocal call_count
        call_count += 1
        return x * 2

    assert expensive_func(5) == 10
    assert expensive_func(5) == 10  # cached
    assert call_count == 1
    print("  [OK] Function caching")

    invalidate_cache()
    assert expensive_func(5) == 10
    assert call_count == 2
    print("  [OK] Cache invalidation")

    print("  Cache tests PASSED")
    return True


def test_circuit_breaker():
    """Test circuit breaker"""
    print("Testing Circuit Breaker...")

    cb = get_circuit_breaker("test_cb", CircuitBreakerConfig(failure_threshold=3, timeout=1))
    cb.reset()

    def fail_func():
        raise ValueError("Test error")

    # Fail 3 times to open circuit
    for i in range(3):
        try:
            cb.call(fail_func)
        except ValueError:
            pass

    assert cb.state.value == "open"
    print("  [OK] Circuit opens after threshold")

    # Rejected when open
    try:
        cb.call(fail_func)
        assert False, "Should have raised"
    except Exception as e:
        assert "OPEN" in str(e)
    print("  [OK] Rejects calls when open")

    # Wait for half-open
    time.sleep(1.1)
    assert cb.state.value == "half_open"
    print("  [OK] Transitions to half-open")

    # Success in half-open
    def succeed_func():
        return "success"

    cb.call(succeed_func)
    cb.call(succeed_func)  # 2 successes = threshold
    assert cb.state.value == "closed"
    print("  [OK] Closes after success threshold")

    print("  Circuit breaker tests PASSED")
    return True


def test_retry():
    """Test retry logic"""
    print("Testing Retry Logic...")

    attempt_count = 0

    @retry(RetryConfig(max_attempts=3, base_delay=0.01))
    def flaky_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("Temporary failure")
        return "success"

    call_count = 0
    result = flaky_func()
    assert result == "success"
    assert call_count == 3
    print("  [OK] Retry on exception")

    # Test retry exhaustion
    call_count = 0

    @retry(RetryConfig(max_attempts=2, base_delay=0.01))
    def always_fail():
        nonlocal call_count
        call_count += 1
        raise ValueError("Permanent failure")

    try:
        always_fail()
        assert False, "Should have raised"
    except Exception as e:
        assert "exhausted" in str(e).lower()
    assert call_count == 2
    print("  [OK] Retry exhaustion")

    print("  Retry tests PASSED")
    return True


def test_metrics():
    """Test metrics collection"""
    print("Testing Metrics...")

    metrics = MetricsCollector()

    # Counters
    metrics.increment("requests_total")
    metrics.increment("requests_total", labels={"endpoint": "/api"})
    # Each label combination is a separate counter in Prometheus style
    assert metrics.get_counter("requests_total") == 1
    assert metrics.get_counter("requests_total", labels={"endpoint": "/api"}) == 1
    print("  [OK] Counters")

    # Gauges
    metrics.set_gauge("memory_usage", 1024)
    assert metrics.get_gauge("memory_usage") == 1024
    print("  [OK] Gauges")

    # Histograms
    for i in range(100):
        metrics.observe("latency", random.uniform(10, 100))
    stats = metrics.get_histogram_stats("latency")
    assert stats["count"] == 100
    assert stats["p50"] > 0
    print("  [OK] Histograms")

    # Latency timer
    with Timer(metrics, "test_op"):
        time.sleep(0.01)
    stats = metrics.get_histogram_stats("test_op_latency_ms")
    assert stats["count"] == 1
    print("  [OK] Latency timer")

    # Prometheus export
    prom_output = metrics.export_prometheus()
    assert "requests_total" in prom_output
    print("  [OK] Prometheus export")

    # Counter shortcuts
    from ultron.performance.metrics import increment_counter, observe_histogram, observe_latency
    increment_counter("test_counter")
    observe_histogram("test_hist", 42)
    observe_latency("test_latency", 0.05)
    print("  [OK] Shortcut functions")

    print("  Metrics tests PASSED")
    return True


def test_health_checks():
    """Test health check framework"""
    print("Testing Health Checks...")

    from ultron.performance.metrics import HealthCheck, run_health_checks

    # Use a fresh HealthCheck instance
    hc = HealthCheck()

    def check_pass():
        return True

    def check_fail():
        return False

    def check_slow():
        time.sleep(0.1)
        return True

    hc.register("test_pass", check_pass)
    hc.register("test_fail", check_fail)
    hc.register("test_slow", check_slow)

    result = hc.run_check("test_pass")
    assert result["status"] == "healthy"
    print("  [OK] Passing check")

    result = hc.run_check("test_fail")
    assert result["status"] == "unhealthy"
    print("  [OK] Failing check")

    all_results = hc.run_all()
    assert all_results["status"] == "unhealthy"  # one fails
    print("  [OK] Run all checks")

    # Test global registry
    from ultron.performance.metrics import _health_checks
    _health_checks.register("decorated_check", lambda: True)
    result = run_health_checks()
    assert "decorated_check" in result["checks"]
    print("  [OK] Health check decorator")

    print("  Health check tests PASSED")
    return True


def test_structured_logging():
    """Test structured logging"""
    print("Testing Structured Logging...")

    from typing import Callable
    from ultron.logging.structured import get_logger, initialize_logging, log_function_call

    # Initialize
    initialize_logging({"log_level": "DEBUG", "log_format": "json"})

    logger = get_logger("test_module", component="test")

    # Test logging
    logger.info("Test message", extra={"key": "value"})
    logger.warning("Warning message")
    logger.error("Error message")

    # Test decorator
    @log_function_call
    def test_func(x):
        return x * 2

    result = test_func(5)
    assert result == 10
    print("  [OK] Function call logging")

    # Test context
    with logger.context(user_id="123", request_id="abc"):
        logger.info("With context")

    print("  Logging tests PASSED")
    return True


def test_resilience_integration():
    """Test resilience patterns together"""
    print("Testing Resilience Integration...")

    from ultron.performance.resilience import circuit_breaker, CircuitBreakerOpenError

    cb = get_circuit_breaker("integration_test", CircuitBreakerConfig(failure_threshold=2))

    @circuit_breaker("integration_test")
    @retry(RetryConfig(max_attempts=3, base_delay=0.01, excluded_exceptions=(CircuitBreakerOpenError,)))
    def integrated_func(should_fail: bool):
        if should_fail:
            raise ConnectionError("Service unavailable")
        return "OK"

    # Success case
    result = integrated_func(False)
    assert result == "OK"
    print("  [OK] Success path")

    # Failure triggers circuit breaker
    for _ in range(2):
        try:
            integrated_func(True)
        except Exception:
            pass

    # Circuit should be open
    try:
        integrated_func(False)
        assert False, "Should have raised"
    except Exception as e:
        assert "OPEN" in str(e)
    print("  [OK] Circuit breaker blocks after failures")

    print("  Resilience integration PASSED")
    return True


def run_all_tests():
    """Run all Phase 8 tests"""
    print("=" * 60)
    print("ULTRON Phase 8 - Production Hardening Tests")
    print("=" * 60)

    tests = [
        test_cache,
        test_circuit_breaker,
        test_retry,
        test_metrics,
        test_health_checks,
        test_structured_logging,
        test_resilience_integration,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)