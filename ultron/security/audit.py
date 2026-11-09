"""ULTRON Audit Logging"""
import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum
import queue


class AuditEventType(Enum):
    TOOL_INVOKED = "TOOL_INVOKED"
    TOOL_RESULT = "TOOL_RESULT"
    PERMISSION_REQUESTED = "PERMISSION_REQUESTED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    WORKSPACE_SELECTED = "WORKSPACE_SELECTED"
    WORKSPACE_ACCESSED = "WORKSPACE_ACCESSED"
    MODEL_LOADED = "MODEL_LOADED"
    MODEL_UNLOADED = "MODEL_UNLOADED"
    MODEL_INFERENCE = "MODEL_INFERENCE"
    VOICE_INPUT = "VOICE_INPUT"
    VOICE_OUTPUT = "VOICE_OUTPUT"
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    FILE_DELETE = "FILE_DELETE"
    APP_LAUNCHED = "APP_LAUNCHED"
    ERROR = "ERROR"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"


class AuditSeverity(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class AuditEntry:
    event_id: str
    timestamp: float
    event_type: AuditEventType
    severity: AuditSeverity
    workspace_id: Optional[str]
    user_id: str = "local_user"
    tool_name: Optional[str] = None
    action: Optional[str] = None
    target: Optional[str] = None
    permission_level: Optional[str] = None
    result: Optional[str] = None  # "success", "failure", "denied"
    details: Dict = field(default_factory=dict)
    verification: Dict = field(default_factory=dict)  # Evidence of actual result

    def to_json(self) -> str:
        data = asdict(self)
        data['event_type'] = self.event_type.value
        data['severity'] = self.severity.value
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> 'AuditEntry':
        data = json.loads(json_str)
        data['event_type'] = AuditEventType(data['event_type'])
        data['severity'] = AuditSeverity(data['severity'])
        return cls(**data)


class AuditLogger:
    """Thread-safe audit logger with rotation"""

    def __init__(self, log_path: Path, max_size_mb: int = 10, max_files: int = 10):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_files = max_files
        self._queue = queue.Queue()
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._current_size = 0

    def start(self):
        """Start background logging thread"""
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._writer_loop, daemon=True)
            self._thread.start()

    def stop(self):
        """Stop background logging thread"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def log(self, entry: AuditEntry):
        """Queue an audit entry for writing"""
        self._queue.put(entry)

    def log_event(self,
                  event_type: AuditEventType,
                  severity: AuditSeverity = AuditSeverity.INFO,
                  workspace_id: Optional[str] = None,
                  tool_name: Optional[str] = None,
                  action: Optional[str] = None,
                  target: Optional[str] = None,
                  permission_level: Optional[str] = None,
                  result: Optional[str] = None,
                  details: Optional[Dict] = None,
                  verification: Optional[Dict] = None):
        """Convenience method to log an event"""
        entry = AuditEntry(
            event_id=f"evt_{int(time.time()*1000000)}",
            timestamp=time.time(),
            event_type=event_type,
            severity=severity,
            workspace_id=workspace_id,
            tool_name=tool_name,
            action=action,
            target=target,
            permission_level=permission_level,
            result=result,
            details=details or {},
            verification=verification or {},
        )
        self.log(entry)

    def _writer_loop(self):
        """Background writer loop"""
        while self._running:
            try:
                entry = self._queue.get(timeout=0.1)
                self._write_entry(entry)
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                # Fallback: print to stderr
                print(f"Audit log error: {e}", file=__import__('sys').stderr)

    def _write_entry(self, entry: AuditEntry):
        """Write entry to log file with rotation"""
        with self._lock:
            # Check rotation
            if self.log_path.exists() and self._current_size > self.max_size_bytes:
                self._rotate()

            # Write entry
            line = entry.to_json() + '\n'
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(line)
            self._current_size += len(line.encode('utf-8'))

    def _rotate(self):
        """Rotate log files"""
        for i in range(self.max_files - 1, 0, -1):
            old = self.log_path.with_suffix(f'.{i}.log')
            new = self.log_path.with_suffix(f'.{i+1}.log')
            if old.exists():
                if new.exists():
                    new.unlink()
                old.rename(new)

        # Move current to .1.log
        rotated = self.log_path.with_suffix('.1.log')
        if rotated.exists():
            rotated.unlink()
        self.log_path.rename(rotated)
        self._current_size = 0

    def query(self,
              event_type: Optional[AuditEventType] = None,
              workspace_id: Optional[str] = None,
              since: Optional[float] = None,
              limit: int = 100) -> list[AuditEntry]:
        """Query audit log (reads current + rotated files)"""
        entries = []
        files = [self.log_path] + sorted(
            self.log_path.parent.glob(self.log_path.name + '.*.log'),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for f in files:
            if not f.exists():
                continue
            with open(f, 'r', encoding='utf-8') as fp:
                for line in fp:
                    if not line.strip():
                        continue
                    try:
                        entry = AuditEntry.from_json(line)
                        if event_type and entry.event_type != event_type:
                            continue
                        if workspace_id and entry.workspace_id != workspace_id:
                            continue
                        if since and entry.timestamp < since:
                            continue
                        entries.append(entry)
                        if len(entries) >= limit:
                            return entries
                    except Exception:
                        continue
        return entries


# Global instance (initialized from config)
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        from ultron.config import config
        cfg = config.config
        log_path = config.resolve_path(cfg.security.audit_log_path)
        _audit_logger = AuditLogger(log_path)
        if cfg.security.audit_log_enabled:
            _audit_logger.start()
    return _audit_logger


def audit_log(event_type: AuditEventType, **kwargs):
    """Convenience function for quick logging"""
    get_audit_logger().log_event(event_type, **kwargs)