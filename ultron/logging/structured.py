"""ULTRON Structured Logging"""
import logging
import json
import sys
import threading
import time
from typing import Dict, Any, Optional, Any, Callable
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from functools import wraps
from dataclasses import dataclass, field
from enum import Enum

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging"""

    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            record.exc_info
            self.formatException(record.exc_info)

        # Add extra fields
        if self.include_extra:
            for key, value in record.__dict__.items():
                if key not in ('name', 'msg', 'args', 'created', 'filename', 'funcName',
                               'levelname', 'levelno', 'lineno', 'module', 'msecs',
                               'message', 'msg', 'name', 'pathname', 'process',
                               'processName', 'relativeCreated', 'thread',
                               'threadName', 'exc_info', 'exc_text', 'stack_info'):
                    record.__dict__[key] = value

        return json.dumps(log_entry, default=str)


class ContextFilter(logging.Filter):
    """Add contextual information to log records"""

    def __init__(self):
        super().__init__()
        self._context = {}
        self._lock = threading.Lock()

    def set_context(self, **kwargs):
        with self._lock:
            self._context.update(kwargs)

    def clear_context(self):
        with self._lock:
            self._context.clear()

    def filter(self, record: logging.LogRecord) -> bool:
        with self._lock:
            for key, value in self._context.items():
                setattr(record, key, value)
        return True


class LoggingManager:
    """Centralized logging management"""

    def __init__(self):
        self._loggers: Dict[str, logging.Logger] = {}
        self._context_filter = ContextFilter()
        self._lock = threading.RLock()
        self._initialized = False

    def initialize(self, config: Dict[str, Any] = None):
        """Initialize logging system"""
        with self._lock:
            if self._initialized:
                return

            cfg = config or {}
            log_level = cfg.get("log_level", "INFO")
            log_format = cfg.get("log_format", "json")  # json or text
            log_file = cfg.get("log_file")
            console_output = cfg.get("console_output", True)

            # Root logger
            root_logger = logging.getLogger()
            root_logger.setLevel(getattr(logging, log_level.upper()))

            # Clear existing handlers
            root_logger.handlers.clear()

            # Add context filter
            root_logger.addFilter(self._context_filter)

            # Console handler
            if console_output:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(getattr(logging, log_level.upper()))
                if log_format == "json":
                    console_handler.setFormatter(StructuredFormatter())
                else:
                    console_handler.setFormatter(
                        logging.Formatter(
                            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S"
                        )
                    )
                root_logger.addHandler(console_handler)

            # File handler
            if log_file:
                log_path = Path(log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(log_file, encoding='utf-8')
                file_handler.setLevel(getattr(logging, log_level.upper()))
                file_handler.setFormatter(StructuredFormatter())
                root_logger.addHandler(file_handler)

            # Audit log handler
            audit_file = cfg.get("audit_log_file", "logs/audit.log")
            audit_path = Path(audit_file)
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_handler = logging.FileHandler(audit_path, encoding='utf-8')
            audit_handler.setLevel(logging.INFO)
            audit_handler.setFormatter(StructuredFormatter())
            audit_handler.addFilter(lambda r: "audit" in r.getMessage().lower() or hasattr(r, 'audit_event'))
            root_logger.addHandler(audit_handler)

            self._initialized = True

    def get_logger(self, name: str) -> logging.Logger:
        """Get or create logger"""
        with self._lock:
            if name not in self._loggers:
                logger = logging.getLogger(name)
                self._loggers[name] = logger
            return self._loggers[name]

    def set_context(self, **kwargs):
        """Set global logging context"""
        self._context_filter.set_context(**kwargs)

    def clear_context(self):
        self._context_filter.clear_context()

    @contextmanager
    def context(self, **kwargs):
        """Context manager for temporary logging context"""
        old_context = {}
        with self._context_filter._lock:
            old_context = self._context_filter._context.copy()
            self._context_filter._context.update(kwargs)
        try:
            yield
        finally:
            with self._context_filter._lock:
                self._context_filter._context.clear()
                self._context_filter._context.update(old_context)


class LoggerAdapter:
    """Adapter for adding contextual information to logs"""

    def __init__(self, logger: logging.Logger, extra: Dict[str, Any] = None):
        self.logger = logger
        self.extra = extra or {}

    def _log(self, level: int, msg: str, **kwargs):
        extra = {**self.extra, **kwargs.pop('extra', {})}
        self.logger.log(level, msg, extra=extra, **kwargs)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, **kwargs)

    def exception(self, msg: str, **kwargs):
        kwargs['exc_info'] = True
        self._log(logging.ERROR, msg, **kwargs)

    def log_with_context(self, level: int, msg: str, context: Dict[str, Any] = None, **kwargs):
        """Log with additional context"""
        extra = {}
        if self.extra:
            extra.update(self.extra)
        if context:
            extra.update(context)
        extra.update(kwargs.pop('extra', {}))
        self.logger.log(level, msg, extra=extra, **kwargs)

    def bind(self, **kwargs) -> 'LoggerAdapter':
        """Create new adapter with additional context"""
        new_extra = {**self.extra, **kwargs}
        return LoggerAdapter(self.logger, new_extra)

    @contextmanager
    def context(self, **kwargs):
        """Context manager for temporary logging context"""
        old_extra = self.extra
        self.extra = {**self.extra, **kwargs}
        try:
            yield self
        finally:
            self.extra = old_extra


def get_logger(name: str = None, **context) -> LoggerAdapter:
    """Get logger adapter with context"""
    manager = _logging_manager
    logger_name = name or "ultron"
    logger = manager.get_logger(logger_name)
    return LoggerAdapter(logger, context)


def setup_logging(config: Dict[str, Any] = None):
    """Initialize logging system"""
    _logging_manager.initialize(config)


# Global logging manager
_logging_manager = LoggingManager()


def get_logger(name: str = None, **context) -> LoggerAdapter:
    """Get logger with context"""
    manager = _logging_manager
    logger_name = name or "ultron"
    logger = manager.get_logger(logger_name)
    return LoggerAdapter(logger, context)


def log_function_call(logger: LoggerAdapter = None, level: int = logging.DEBUG):
    """Decorator to log function calls"""
    # Handle both @log_function_call and @log_function_call()
    if callable(logger):
        # Used as @log_function_call without parentheses
        func = logger
        logger = None
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            log = get_logger(func.__module__)
            log.debug(f"Calling {func.__name__}", extra={"args": str(args)[:200], "kwargs": str(kwargs)[:200]})
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start
                log.debug(f"{func.__name__} completed in {duration:.3f}s", extra={"duration_ms": duration * 1000})
                return result
            except Exception as e:
                duration = time.time() - start
                log.error(f"{func.__name__} failed after {duration:.3f}s: {e}", exc_info=True)
                raise
        return wrapper
    
    # Used as @log_function_call() or @log_function_call(logger=..., level=...)
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            log = logger or get_logger(func.__module__)
            log.debug(f"Calling {func.__name__}", extra={"args": str(args)[:200], "kwargs": str(kwargs)[:200]})
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start
                log.debug(f"{func.__name__} completed in {duration:.3f}s", extra={"duration_ms": duration * 1000})
                return result
            except Exception as e:
                duration = time.time() - start
                log = logger or get_logger(func.__module__)
                log.error(f"{func.__name__} failed after {duration:.3f}s: {e}", exc_info=True)
                raise
            return wrapper
        return decorator


def log_execution_time(logger: LoggerAdapter = None, level: int = logging.INFO):
    """Decorator to log execution time"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            log = logger or get_logger(func.__module__)
            start = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                duration = time.time() - start
                log.log(level, f"{func.__name__} took {duration:.3f}s",
                        extra={"function": func.__name__, "duration_ms": duration * 1000})
        return wrapper
    return decorator


def log_async_execution(logger: LoggerAdapter = None):
    """Decorator for async function timing"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            log = logger or get_logger(func.__module__)
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.time() - start
                log.debug(f"{func.__name__} took {duration:.3f}s",
                          extra={"function": func.__name__, "duration_ms": duration * 1000})
        return wrapper
    return decorator


# Initialize default logging
def initialize_logging(config: Dict[str, Any] = None):
    """Initialize default logging configuration"""
    default_config = {
        "log_level": "INFO",
        "log_format": "json",
        "log_file": "logs/ultron.log",
        "console_output": True,
        "audit_log_file": "logs/audit.log",
    }
    if config:
        default_config.update(config)
    _logging_manager.initialize(default_config)


# Initialize on import
initialize_logging()