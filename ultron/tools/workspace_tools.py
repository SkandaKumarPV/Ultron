"""ULTRON Workspace Tools - Query, index, manage workspaces"""
from pathlib import Path
from typing import List, Dict, Any, Optional

from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.security.permissions import PermissionLevel
from ultron.workspace.selector import workspace_manager
from ultron.workspace.analyzer import workspace_analyzer
from ultron.workspace.parser import parser_registry
from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class SelectWorkspaceTool(BaseTool):
    """Select a workspace for analysis"""
    name = "select_workspace"
    description = "Select a workspace by ID or create a new one from a folder path"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="workspace_id", type="string", description="Existing workspace ID", required=False),
        ToolParameter(name="path", type="string", description="Folder path to create workspace from", required=False),
        ToolParameter(name="name", type="string", description="Workspace name (if creating new)", required=False),
        ToolParameter(name="read_only", type="boolean", description="Open in read-only mode", required=False, default=True),
    ]

    def execute(self, workspace_id: str = None, path: str = None, name: str = None, read_only: bool = True) -> ToolResult:
        if workspace_id:
            # Select existing
            success = workspace_manager.select_workspace(workspace_id)
            if success:
                ws = workspace_manager.get_workspace(workspace_id)
                return ToolResult(
                    success=True,
                    data=f"Selected workspace: {ws.name} ({workspace_id})",
                    verification={"workspace_id": workspace_id, "name": ws.name}
                )
            return ToolResult(success=False, error=f"Workspace not found: {workspace_id}")

        if path:
            # Create new
            try:
                ws = workspace_manager.create_workspace(name or "Workspace", path, read_only)
                workspace_manager.select_workspace(ws.workspace_id)
                return ToolResult(
                    success=True,
                    data=f"Created and selected workspace: {ws.name} ({ws.workspace_id})",
                    verification={"workspace_id": ws.workspace_id, "name": ws.name, "path": ws.path}
                )
            except Exception as e:
                return ToolResult(success=False, error=f"Failed to create workspace: {e}")

        return ToolResult(success=False, error="Either workspace_id or path required")


class ListWorkspacesTool(BaseTool):
    """List all workspaces"""
    name = "list_workspaces"
    description = "List all configured workspaces"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = []

    def execute(self) -> ToolResult:
        workspaces = workspace_manager.list_workspaces()
        current_id = workspace_manager.get_current_workspace_id()

        results = []
        for ws in workspaces:
            results.append({
                "workspace_id": ws.workspace_id,
                "name": ws.name,
                "path": ws.path,
                "read_only": ws.read_only,
                "file_count": ws.file_count,
                "index_status": ws.index_status,
                "current": ws.workspace_id == current_id,
            })

        return ToolResult(
            success=True,
            data={"workspaces": results, "current": current_id},
            verification={"count": len(results)}
        )


class QueryWorkspaceTool(BaseTool):
    """Query the current workspace with a question"""
    name = "query_workspace"
    description = "Ask a question about the current workspace documents"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.SEARCH
    parameters = [
        ToolParameter(name="question", type="string", description="Question to ask about workspace content"),
    ]

    def execute(self, question: str) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected. Use select_workspace first.")

        # Check index status
        ws = workspace_manager.get_workspace(ws_id)
        if ws.index_status != "ready":
            return ToolResult(
                success=False,
                error=f"Workspace not indexed (status: {ws.index_status}). Run index_workspace first."
            )

        result = workspace_analyzer.query(ws_id, question)

        return ToolResult(
            success=True,
            data={
                "answer": result.answer,
                "sources": result.sources,
                "confidence": result.confidence,
            },
            verification={"workspace_id": ws_id, "question": question, "source_count": len(result.sources)}
        )


class IndexWorkspaceTool(BaseTool):
    """Index the current workspace for search"""
    name = "index_workspace"
    description = "Index all documents in the current workspace for search and query"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.CREATE
    parameters = [
        ToolParameter(name="workspace_id", type="string", description="Workspace ID (default: current)", required=False),
    ]

    def execute(self, workspace_id: str = None) -> ToolResult:
        target_id = workspace_id or workspace_manager.get_current_workspace_id()
        if not target_id:
            return ToolResult(success=False, error="No workspace specified or selected")

        # Run indexing (this may take a while)
        result = workspace_analyzer.index_workspace(target_id)

        return ToolResult(
            success=result.get("success", False),
            data=result,
            verification={"workspace_id": target_id}
        )


class ListWorkspaceFilesTool(BaseTool):
    """List files in workspace"""
    name = "list_workspace_files"
    description = "List files in the current workspace"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="extension", type="string", description="Filter by file extension (e.g., .py, .pdf)", required=False),
        ToolParameter(name="limit", type="integer", description="Maximum files to return", required=False, default=100),
    ]

    def execute(self, extension: str = None, limit: int = 100) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_path = Path(ws.path)

        if not ws_path.exists():
            return ToolResult(success=False, error=f"Workspace path not found: {ws_path}")

        supported_exts = set(config.config.workspace.supported_extensions)
        if extension:
            if not extension.startswith('.'):
                extension = '.' + extension
            supported_exts = {extension}

        files = []
        for ext in supported_exts:
            for f in ws_path.rglob(f"*{ext}"):
                if f.is_file():
                    try:
                        stat = f.stat()
                        if stat.st_size <= config.config.workspace.max_file_size_mb * 1024 * 1024:
                            files.append({
                                "name": f.name,
                                "path": str(f),
                                "relative_path": str(f.relative_to(ws_path)),
                                "size": stat.st_size,
                                "extension": f.suffix,
                                "modified": stat.st_mtime,
                            })
                    except Exception:
                        pass

        files.sort(key=lambda x: x["modified"], reverse=True)
        files = files[:limit]

        return ToolResult(
            success=True,
            data={"files": files, "count": len(files)},
            verification={"workspace_id": ws_id, "extensions": list(supported_exts)}
        )


# Register workspace tools
tool_registry.register(SelectWorkspaceTool())
tool_registry.register(ListWorkspacesTool())
tool_registry.register(QueryWorkspaceTool())
tool_registry.register(IndexWorkspaceTool())
tool_registry.register(ListWorkspaceFilesTool())