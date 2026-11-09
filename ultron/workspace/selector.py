"""ULTRON Workspace Selection and Management"""
import os
import json
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
import time

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


@dataclass
class Workspace:
    workspace_id: str
    name: str
    path: str
    read_only: bool = True
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    file_count: int = 0
    index_status: str = "pending"  # pending, indexing, ready, error
    metadata: Dict = field(default_factory=dict)


class WorkspaceManager:
    """Manages workspace selection, authorization, and isolation"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._workspaces: Dict[str, Workspace] = {}
        self._current_workspace_id: Optional[str] = None
        self._workspace_dir = config.resolve_path("workspaces")
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        self._load_workspaces()
        self._initialized = True

    def _load_workspaces(self):
        """Load workspace metadata from disk"""
        for ws_dir in self._workspace_dir.iterdir():
            if ws_dir.is_dir():
                meta_file = ws_dir / "workspace.json"
                if meta_file.exists():
                    try:
                        with open(meta_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        ws = Workspace(**data)
                        self._workspaces[ws.workspace_id] = ws
                    except Exception:
                        pass

    def _save_workspace(self, workspace: Workspace):
        """Save workspace metadata to disk"""
        ws_dir = self._workspace_dir / workspace.workspace_id
        ws_dir.mkdir(parents=True, exist_ok=True)
        meta_file = ws_dir / "workspace.json"
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(asdict(workspace), f, indent=2)

    def create_workspace(self, name: str, path: str, read_only: bool = True) -> Workspace:
        """Create a new workspace"""
        # Validate path
        path_obj = Path(path).resolve()
        if not path_obj.exists():
            raise ValueError(f"Path does not exist: {path}")

        # Generate workspace ID
        import hashlib
        ws_id = hashlib.sha256(f"{name}:{path}".encode()).hexdigest()[:16]

        # Check for existing
        if ws_id in self._workspaces:
            return self._workspaces[ws_id]

        # Count files
        file_count = self._count_files(path_obj)

        workspace = Workspace(
            workspace_id=ws_id,
            name=name,
            path=str(path_obj),
            read_only=read_only,
            file_count=file_count,
        )

        self._workspaces[ws_id] = workspace
        self._save_workspace(workspace)

        audit_log(AuditEventType.WORKSPACE_SELECTED, severity=AuditSeverity.INFO,
                  workspace_id=ws_id, details={"name": name, "path": path, "read_only": read_only})

        return workspace

    def _count_files(self, path: Path) -> int:
        """Count supported files in workspace"""
        cfg = config.config
        extensions = set(cfg.workspace.supported_extensions)
        count = 0
        try:
            for f in path.rglob("*"):
                if f.is_file() and f.suffix.lower() in extensions:
                    if f.stat().st_size <= cfg.workspace.max_file_size_mb * 1024 * 1024:
                        count += 1
        except Exception:
            pass
        return count

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Get workspace by ID"""
        return self._workspaces.get(workspace_id)

    def get_current_workspace(self) -> Optional[Workspace]:
        """Get currently selected workspace"""
        if self._current_workspace_id:
            return self._workspaces.get(self._current_workspace_id)
        return None

    def get_current_workspace_id(self) -> Optional[str]:
        return self._current_workspace_id

    def select_workspace(self, workspace_id: str) -> bool:
        """Select a workspace as current"""
        if workspace_id not in self._workspaces:
            return False

        self._current_workspace_id = workspace_id
        ws = self._workspaces[workspace_id]
        ws.last_accessed = time.time()
        self._save_workspace(ws)

        audit_log(AuditEventType.WORKSPACE_SELECTED, workspace_id=workspace_id,
                  severity=AuditSeverity.INFO, details={"action": "selected"})

        return True

    def list_workspaces(self) -> List[Workspace]:
        """List all workspaces"""
        return list(self._workspaces.values())

    def remove_workspace(self, workspace_id: str) -> bool:
        """Remove a workspace"""
        if workspace_id in self._workspaces:
            # Don't delete files, just remove metadata
            del self._workspaces[workspace_id]
            meta_file = self._workspace_dir / workspace_id / "workspace.json"
            if meta_file.exists():
                meta_file.unlink()

            if self._current_workspace_id == workspace_id:
                self._current_workspace_id = None

            audit_log(AuditEventType.WORKSPACE_SELECTED, workspace_id=workspace_id,
                      severity=AuditSeverity.INFO, details={"action": "removed"})
            return True
        return False

    def is_path_authorized(self, workspace_id: str, file_path: str) -> bool:
        """Check if a file path is within the authorized workspace"""
        ws = self.get_workspace(workspace_id)
        if not ws:
            return False

        try:
            ws_path = Path(ws.path).resolve()
            target_path = Path(file_path).resolve()
            return target_path.is_relative_to(ws_path)
        except Exception:
            return False

    def get_workspace_path(self, workspace_id: str) -> Optional[Path]:
        """Get workspace root path"""
        ws = self.get_workspace(workspace_id)
        if ws:
            return Path(ws.path)
        return None

    def update_index_status(self, workspace_id: str, status: str):
        """Update workspace index status"""
        ws = self.get_workspace(workspace_id)
        if ws:
            ws.index_status = status
            self._save_workspace(ws)


# Global instance
workspace_manager = WorkspaceManager()