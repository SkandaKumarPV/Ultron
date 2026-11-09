"""ULTRON Tool Registry"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Awaitable
from enum import Enum
import inspect
import asyncio
import time
import threading

from ultron.security.permissions import PermissionLevel, PermissionRegistry, PermissionRequest, permission_registry
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity
from ultron.config import config


class ToolCategory(Enum):
    VOICE = "voice"
    WORKSPACE = "workspace"
    PC_CONTROL = "pc_control"
    MEMORY = "memory"
    MODEL = "model"
    SYSTEM = "system"


@dataclass
class ToolParameter:
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    verification: Dict = field(default_factory=dict)  # Evidence of actual result
    metadata: Dict = field(default_factory=dict)


class BaseTool(ABC):
    """Base class for all ULTRON tools"""

    name: str = ""
    description: str = ""
    category: ToolCategory = ToolCategory.SYSTEM
    permission_level: PermissionLevel = PermissionLevel.READ
    parameters: List[ToolParameter] = field(default_factory=list)

    def __init__(self):
        self._verify_permission_registered()

    def _verify_permission_registered(self):
        """Ensure tool is registered in permission registry"""
        if self.name:
            permission_registry.register_tool(self.name, self.permission_level)

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool synchronously"""
        pass

    async def execute_async(self, **kwargs) -> ToolResult:
        """Execute the tool asynchronously (default: run in thread pool)"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.execute(**kwargs))

    def get_schema(self) -> Dict:
        """Get tool schema for LLM function calling"""
        props = {}
        required = []
        for param in self.parameters:
            props[param.name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                props[param.name]["enum"] = param.enum
            if param.required:
                required.append(param.name)
            elif param.default is not None:
                props[param.name]["default"] = param.default

        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }

    def validate_args(self, kwargs: Dict) -> Dict:
        """Validate and coerce arguments"""
        validated = {}
        for param in self.parameters:
            if param.name in kwargs:
                validated[param.name] = kwargs[param.name]
            elif param.required:
                raise ValueError(f"Missing required parameter: {param.name}")
            elif param.default is not None:
                validated[param.name] = param.default
        return validated


class ToolRegistry:
    """Central registry for all ULTRON tools"""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._categories: Dict[ToolCategory, List[str]] = {c: [] for c in ToolCategory}
        self._lock = threading.RLock()
        self._execution_history: List[Dict] = []
        self._max_history = 1000

    def register(self, tool: BaseTool) -> BaseTool:
        """Register a tool instance"""
        with self._lock:
            if tool.name in self._tools:
                raise ValueError(f"Tool already registered: {tool.name}")
            self._tools[tool.name] = tool
            self._categories[tool.category].append(tool.name)
            # Ensure permission is registered
            permission_registry.register_tool(tool.name, tool.permission_level)
            return tool

    def unregister(self, tool_name: str):
        """Unregister a tool"""
        with self._lock:
            tool = self._tools.pop(tool_name, None)
            if tool:
                self._categories[tool.category].remove(tool_name)

    def get(self, tool_name: str) -> Optional[BaseTool]:
        """Get a tool by name"""
        with self._lock:
            return self._tools.get(tool_name)

    def get_all(self) -> List[BaseTool]:
        """Get all registered tools"""
        with self._lock:
            return list(self._tools.values())

    def get_by_category(self, category: ToolCategory) -> List[BaseTool]:
        """Get tools by category"""
        with self._lock:
            return [self._tools[name] for name in self._categories[category] if name in self._tools]

    def get_schemas(self, category: Optional[ToolCategory] = None) -> List[Dict]:
        """Get function calling schemas for all tools (or category)"""
        with self._lock:
            tools = self.get_by_category(category) if category else self.get_all()
            return [tool.get_schema() for tool in tools]

    def execute(self,
                tool_name: str,
                workspace_id: Optional[str] = None,
                user_id: str = "local_user",
                **kwargs) -> ToolResult:
        """Execute a tool with permission check and audit logging"""
        tool = self.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Tool not found: {tool_name}")

        # Check permission
        required_level = tool.permission_level
        request = PermissionRequest(
            tool_name=tool_name,
            action="execute",
            target=str(kwargs),
            required_level=required_level,
            workspace_id=workspace_id,
        )

        grant = permission_registry.request_permission(request)

        # Audit: permission requested
        audit_log(
            AuditEventType.PERMISSION_REQUESTED,
            workspace_id=workspace_id,
            tool_name=tool_name,
            action="execute",
            target=str(kwargs),
            permission_level=required_level.value,
            result="granted" if grant.granted else "denied",
        )

        if not grant.granted:
            audit_log(
                AuditEventType.PERMISSION_DENIED,
                workspace_id=workspace_id,
                tool_name=tool_name,
                severity=AuditSeverity.WARNING,
            )
            return ToolResult(
                success=False,
                error=f"Permission denied for {tool_name} (requires {required_level.value})",
            )

        # Audit: tool invoked
        start_time = time.time()
        audit_log(
            AuditEventType.TOOL_INVOKED,
            workspace_id=workspace_id,
            tool_name=tool_name,
            action="execute",
            target=str(kwargs),
            permission_level=required_level.value,
        )

        try:
            # Validate arguments
            validated_args = tool.validate_args(kwargs)

            # Execute tool
            result = tool.execute(**validated_args)

            # Record execution
            exec_time = time.time() - start_time
            self._record_execution(tool_name, workspace_id, validated_args, result, exec_time)

            # Audit: tool result
            audit_log(
                AuditEventType.TOOL_RESULT,
                workspace_id=workspace_id,
                tool_name=tool_name,
                action="execute",
                result="success" if result.success else "failure",
                verification=result.verification,
                details={"execution_time_ms": exec_time * 1000, "error": result.error},
            )

            return result

        except Exception as e:
            exec_time = time.time() - start_time
            error_result = ToolResult(success=False, error=str(e))
            self._record_execution(tool_name, workspace_id, kwargs, error_result, exec_time)

            audit_log(
                AuditEventType.TOOL_RESULT,
                workspace_id=workspace_id,
                tool_name=tool_name,
                action="execute",
                result="error",
                severity=AuditSeverity.ERROR,
                details={"execution_time_ms": exec_time * 1000, "error": str(e)},
            )

            return error_result

    async def execute_async(self,
                            tool_name: str,
                            workspace_id: Optional[str] = None,
                            user_id: str = "local_user",
                            **kwargs) -> ToolResult:
        """Execute a tool asynchronously"""
        tool = self.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Tool not found: {tool_name}")

        # Check permission (same as sync)
        required_level = tool.permission_level
        request = PermissionRequest(
            tool_name=tool_name,
            action="execute",
            target=str(kwargs),
            required_level=required_level,
            workspace_id=workspace_id,
        )
        grant = permission_registry.request_permission(request)

        audit_log(
            AuditEventType.PERMISSION_REQUESTED,
            workspace_id=workspace_id,
            tool_name=tool_name,
            action="execute",
            permission_level=required_level.value,
            result="granted" if grant.granted else "denied",
        )

        if not grant.granted:
            return ToolResult(
                success=False,
                error=f"Permission denied for {tool_name} (requires {required_level.value})",
            )

        audit_log(
            AuditEventType.TOOL_INVOKED,
            workspace_id=workspace_id,
            tool_name=tool_name,
        )

        start_time = time.time()
        try:
            validated_args = tool.validate_args(kwargs)
            result = await tool.execute_async(**validated_args)
            exec_time = time.time() - start_time

            self._record_execution(tool_name, workspace_id, validated_args, result, exec_time)

            audit_log(
                AuditEventType.TOOL_RESULT,
                workspace_id=workspace_id,
                tool_name=tool_name,
                result="success" if result.success else "failure",
                verification=result.verification,
                details={"execution_time_ms": exec_time * 1000},
            )

            return result

        except Exception as e:
            exec_time = time.time() - start_time
            error_result = ToolResult(success=False, error=str(e))
            self._record_execution(tool_name, workspace_id, kwargs, error_result, exec_time)

            audit_log(
                AuditEventType.TOOL_RESULT,
                workspace_id=workspace_id,
                tool_name=tool_name,
                result="error",
                severity=AuditSeverity.ERROR,
                details={"execution_time_ms": exec_time * 1000, "error": str(e)},
            )
            return error_result

    def _record_execution(self, tool_name: str, workspace_id: Optional[str], args: Dict, result: ToolResult, exec_time: float):
        """Record execution in history"""
        with self._lock:
            self._execution_history.append({
                "timestamp": time.time(),
                "tool": tool_name,
                "workspace_id": workspace_id,
                "args": args,
                "success": result.success,
                "exec_time_ms": exec_time * 1000,
            })
            if len(self._execution_history) > self._max_history:
                self._execution_history.pop(0)

    def get_history(self, limit: int = 50) -> List[Dict]:
        """Get recent execution history"""
        with self._lock:
            return self._execution_history[-limit:]


# Global instance
tool_registry = ToolRegistry()