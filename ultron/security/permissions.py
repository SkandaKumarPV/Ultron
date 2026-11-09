"""ULTRON Permission System"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from pathlib import Path
import json
import time
import threading


class PermissionLevel(Enum):
    READ = "READ"
    SEARCH = "SEARCH"
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"
    EXECUTE = "EXECUTE"
    EXTERNAL_TRANSMIT = "EXTERNAL_TRANSMIT"

    @property
    def rank(self) -> int:
        """Higher rank = more powerful"""
        ranks = {
            PermissionLevel.READ: 1,
            PermissionLevel.SEARCH: 2,
            PermissionLevel.CREATE: 3,
            PermissionLevel.MODIFY: 4,
            PermissionLevel.DELETE: 5,
            PermissionLevel.EXECUTE: 6,
            PermissionLevel.EXTERNAL_TRANSMIT: 7,
        }
        return ranks[self]

    def implies(self, other: 'PermissionLevel') -> bool:
        """Does this permission imply the other?"""
        return self.rank >= other.rank


@dataclass
class PermissionRequest:
    tool_name: str
    action: str
    target: str
    required_level: PermissionLevel
    workspace_id: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    request_id: str = field(default_factory=lambda: f"req_{int(time.time()*1000)}")


@dataclass
class PermissionGrant:
    request_id: str
    granted: bool
    level: PermissionLevel
    granted_by: str = "user"  # "user", "config", "auto"
    conditions: Dict = field(default_factory=dict)
    expires_at: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


class PermissionRegistry:
    """Manages tool permissions and grants"""

    def __init__(self):
        self._tool_permissions: Dict[str, PermissionLevel] = {}
        self._workspace_grants: Dict[str, Dict[str, PermissionGrant]] = {}  # workspace_id -> tool -> grant
        self._global_grants: Dict[str, PermissionGrant] = {}
        self._pending_requests: Dict[str, PermissionRequest] = {}
        self._lock = threading.RLock()
        self._confirmation_callback = None

    def register_tool(self, tool_name: str, required_level: PermissionLevel):
        """Register a tool with its required permission level"""
        with self._lock:
            self._tool_permissions[tool_name] = required_level

    def get_required_level(self, tool_name: str) -> PermissionLevel:
        """Get the permission level required for a tool"""
        with self._lock:
            return self._tool_permissions.get(tool_name, PermissionLevel.READ)

    def set_confirmation_callback(self, callback):
        """Set callback for user confirmation prompts"""
        self._confirmation_callback = callback

    def request_permission(self, request: PermissionRequest) -> PermissionGrant:
        """Request permission for a tool action"""
        with self._lock:
            # Check for existing valid grant
            grant = self._check_existing_grant(request)
            if grant:
                return grant

            # Check if auto-granted by config (READ/SEARCH for authorized workspaces)
            # For system tools (no workspace or 'system'), auto-grant all levels
            if request.workspace_id is None or request.workspace_id == "system":
                grant = PermissionGrant(
                    request_id=request.request_id,
                    granted=True,
                    level=request.required_level,
                    granted_by="config",
                )
                self._store_grant(None, request.tool_name, grant)
                return grant
            
            # For non-readonly workspaces, auto-grant MODIFY and below
            if request.required_level in (PermissionLevel.READ, PermissionLevel.SEARCH, PermissionLevel.CREATE, PermissionLevel.MODIFY):
                # Auto-grant if workspace is authorized and not read-only
                ws = None
                try:
                    from ultron.workspace.selector import workspace_manager
                    ws = workspace_manager.get_workspace(request.workspace_id)
                except Exception:
                    pass
                
                if ws and not ws.read_only and self._is_workspace_authorized(request.workspace_id):
                    grant = PermissionGrant(
                        request_id=request.request_id,
                        granted=True,
                        level=request.required_level,
                        granted_by="config",
                    )
                    self._store_grant(request.workspace_id, request.tool_name, grant)
                    return grant
            
            if request.required_level in (PermissionLevel.READ, PermissionLevel.SEARCH):
                # Auto-grant if workspace is authorized
                if self._is_workspace_authorized(request.workspace_id):
                    grant = PermissionGrant(
                        request_id=request.request_id,
                        granted=True,
                        level=request.required_level,
                        granted_by="config",
                    )
                    self._store_grant(request.workspace_id, request.tool_name, grant)
                    return grant

            # Require user confirmation for consequential actions
            if request.required_level in self._get_confirmation_levels():
                grant = self._prompt_user(request)
            else:
                grant = PermissionGrant(
                    request_id=request.request_id,
                    granted=False,
                    level=request.required_level,
                    granted_by="denied",
                )

            self._store_grant(request.workspace_id, request.tool_name, grant)
            return grant

    def _check_existing_grant(self, request: PermissionRequest) -> Optional[PermissionGrant]:
        """Check for existing valid grant"""
        grants = self._workspace_grants.get(request.workspace_id, {}) if request.workspace_id else self._global_grants
        grant = grants.get(request.tool_name)
        if grant and grant.granted:
            if grant.expires_at is None or grant.expires_at > time.time():
                if grant.level.implies(request.required_level):
                    return grant
        return None

    def _is_workspace_authorized(self, workspace_id: str) -> bool:
        """Check if workspace is authorized (simplified - would check actual auth)"""
        return True  # In practice, check against authorized workspace list

    def _get_confirmation_levels(self) -> Set[PermissionLevel]:
        """Get levels that require confirmation"""
        from ultron.config import config
        levels = config.config.permissions.require_confirmation
        return {PermissionLevel(l) for l in levels}

    def _prompt_user(self, request: PermissionRequest) -> PermissionGrant:
        """Prompt user for confirmation"""
        if self._confirmation_callback:
            granted = self._confirmation_callback(request)
        else:
            # Default: deny for safety
            granted = False

        return PermissionGrant(
            request_id=request.request_id,
            granted=granted,
            level=request.required_level,
            granted_by="user" if granted else "denied",
        )

    def _store_grant(self, workspace_id: Optional[str], tool_name: str, grant: PermissionGrant):
        """Store a permission grant"""
        if workspace_id:
            if workspace_id not in self._workspace_grants:
                self._workspace_grants[workspace_id] = {}
            self._workspace_grants[workspace_id][tool_name] = grant
        else:
            self._global_grants[tool_name] = grant

    def revoke_tool_permission(self, tool_name: str, workspace_id: Optional[str] = None):
        """Revoke permission for a tool"""
        with self._lock:
            if workspace_id and workspace_id in self._workspace_grants:
                self._workspace_grants[workspace_id].pop(tool_name, None)
            else:
                self._global_grants.pop(tool_name, None)

    def revoke_workspace_permissions(self, workspace_id: str):
        """Revoke all permissions for a workspace"""
        with self._lock:
            self._workspace_grants.pop(workspace_id, None)

    def get_grant(self, tool_name: str, workspace_id: Optional[str] = None) -> Optional[PermissionGrant]:
        """Get current grant for a tool"""
        with self._lock:
            if workspace_id and workspace_id in self._workspace_grants:
                return self._workspace_grants[workspace_id].get(tool_name)
            return self._global_grants.get(tool_name)


# Global instance
permission_registry = PermissionRegistry()