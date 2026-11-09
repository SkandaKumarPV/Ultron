"""ULTRON Workspace Automation Tools - Code analysis, debugging, test running, doc generation"""
import os
import subprocess
import ast
import json
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.security.permissions import PermissionLevel
from ultron.workspace.selector import workspace_manager
from ultron.workspace.analyzer import workspace_analyzer
from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class ExplainCodeTool(BaseTool):
    """Explain code in a file or snippet"""
    name = "explain_code"
    description = "Explain what a code file or snippet does"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file to explain"),
        ToolParameter(name="focus", type="string", description="Specific function/class to focus on", required=False),
    ]

    def execute(self, file_path: str, focus: str = None) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        # Validate path authorization
        target = Path(file_path).expanduser().resolve()
        if not workspace_manager.is_path_authorized(ws_id, str(target)):
            return ToolResult(success=False, error="Path not authorized in current workspace")

        if not target.exists():
            return ToolResult(success=False, error=f"File not found: {target}")

        try:
            with open(target, 'r', encoding='utf-8', errors='replace') as f:
                code = f.read()
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")

        # Use workspace analyzer to explain
        question = f"Explain this {target.suffix} code"
        if focus:
            question += f", focusing on {focus}"
        question += f":\n\n{code[:5000]}"

        result = workspace_analyzer.query(ws_id, question)

        return ToolResult(
            success=True,
            data={
                "file": str(target),
                "focus": focus,
                "explanation": result.answer,
                "sources": result.sources,
            },
            verification={"file": str(target), "action": "explained"}
        )


class AnalyzeCodeTool(BaseTool):
    """Analyze code structure (imports, functions, classes, complexity)"""
    name = "analyze_code"
    description = "Analyze code structure: imports, functions, classes, complexity metrics"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file to analyze"),
    ]

    def execute(self, file_path: str) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        target = Path(file_path).expanduser().resolve()
        if not workspace_manager.is_path_authorized(ws_id, str(target)):
            return ToolResult(success=False, error="Path not authorized")

        if not target.exists():
            return ToolResult(success=False, error=f"File not found: {target}")

        try:
            with open(target, 'r', encoding='utf-8', errors='replace') as f:
                code = f.read()
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")

        # Parse AST for analysis
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return ToolResult(success=False, error=f"Syntax error in file: {e}")

        analysis = {
            "file": str(target),
            "imports": [],
            "functions": [],
            "classes": [],
            "function_count": 0,
            "class_count": 0,
            "lines_of_code": len(code.splitlines()),
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    analysis["imports"].append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    analysis["imports"].append(f"from {module} import {alias.name}")

            if isinstance(node, ast.FunctionDef):
                analysis["functions"].append({
                    "name": node.name,
                    "args": [a.arg for a in node.args.args],
                    "line": node.lineno,
                    "docstring": ast.get_docstring(node),
                })
                analysis["function_count"] += 1

            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                analysis["classes"].append({
                    "name": node.name,
                    "methods": methods,
                    "line": node.lineno,
                    "docstring": ast.get_docstring(node),
                })
                analysis["class_count"] += 1

        return ToolResult(
            success=True,
            data=analysis,
            verification={"file": str(target), "action": "analyzed"}
        )


class RunTestsTool(BaseTool):
    """Run tests in the workspace"""
    name = "run_tests"
    description = "Run tests in the workspace (pytest, unittest, etc.)"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.EXECUTE
    parameters = [
        ToolParameter(name="test_path", type="string", description="Path to test file or directory", required=False),
        ToolParameter(name="test_framework", type="string", description="Test framework", required=False, default="pytest",
                      enum=["pytest", "unittest", "auto"]),
        ToolParameter(name="args", type="array", description="Additional arguments", required=False, default=[]),
    ]

    def execute(self, test_path: str = None, test_framework: str = "pytest", args: List[str] = None) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_root = Path(ws.path)

        # Determine test command
        if test_framework == "auto":
            # Check for pytest.ini, pyproject.toml, setup.cfg
            if (ws_root / "pytest.ini").exists() or (ws_root / "pyproject.toml").exists():
                test_framework = "pytest"
            else:
                test_framework = "unittest"

        if test_framework == "pytest":
            cmd = ["python", "-m", "pytest"]
        elif test_framework == "unittest":
            cmd = ["python", "-m", "unittest"]
        else:
            return ToolResult(success=False, error=f"Unknown test framework: {test_framework}")

        if test_path:
            target = Path(test_path)
            if not target.is_absolute():
                target = ws_root / target
            if not workspace_manager.is_path_authorized(ws_id, str(target)):
                return ToolResult(success=False, error="Test path not authorized")
            cmd.append(str(target))

        if args:
            cmd.extend(args)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                timeout=300,
            )

            return ToolResult(
                success=result.returncode == 0,
                data={
                    "command": " ".join(cmd),
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                verification={"command": " ".join(cmd), "returncode": result.returncode}
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="Test execution timed out (5 min)")
        except Exception as e:
            return ToolResult(success=False, error=f"Test execution failed: {e}")


class GenerateDocsTool(BaseTool):
    """Generate documentation for code"""
    name = "generate_docs"
    description = "Generate documentation for a file or module"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.CREATE
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file to document"),
        ToolParameter(name="output_path", type="string", description="Output file path (optional)", required=False),
        ToolParameter(name="format", type="string", description="Output format", required=False, default="markdown",
                      enum=["markdown", "rst", "json"]),
    ]

    def execute(self, file_path: str, output_path: str = None, format: str = "markdown") -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        target = Path(file_path).expanduser().resolve()
        if not workspace_manager.is_path_authorized(ws_id, str(target)):
            return ToolResult(success=False, error="Path not authorized")

        if not target.exists():
            return ToolResult(success=False, error=f"File not found: {target}")

        try:
            with open(target, 'r', encoding='utf-8', errors='replace') as f:
                code = f.read()
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")

        # Query analyzer for documentation
        ws_id = workspace_manager.get_current_workspace_id()
        question = f"Generate {format} documentation for this {target.suffix} file. Include: module docstring, class/function signatures, parameters, return types, and usage examples.\n\n{code[:5000]}"

        result = workspace_analyzer.query(ws_id, question)

        # Determine output path
        if output_path:
            out = Path(output_path).expanduser().resolve()
        else:
            out = target.with_suffix(f".doc.{format}")

        # Check output authorization
        if not workspace_manager.is_path_authorized(ws_id, str(out)):
            out = target.with_suffix(f".doc.{format}")

        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, 'w', encoding='utf-8') as f:
                f.write(result.answer)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write documentation: {e}")

        return ToolResult(
            success=True,
            data={
                "source_file": str(target),
                "output_file": str(out),
                "format": format,
                "preview": result.answer[:500],
            },
            verification={"source": str(target), "output": str(out), "action": "documented"}
        )


class SafeWriteTool(BaseTool):
    """Safely write to a file with backup and checkpoint"""
    name = "safe_write"
    description = "Write to a file with automatic backup and checkpoint"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file to write"),
        ToolParameter(name="content", type="string", description="Content to write"),
        ToolParameter(name="create_backup", type="boolean", description="Create backup before writing", required=False, default=True),
    ]

    def execute(self, file_path: str, content: str, create_backup: bool = True) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        target = Path(file_path).expanduser().resolve()
        if not workspace_manager.is_path_authorized(ws_id, str(target)):
            return ToolResult(success=False, error="Path not authorized")

        backup_path = None
        if create_backup and target.exists():
            # Create timestamped backup
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = target.parent / ".ultron_backups"
            backup_dir.mkdir(exist_ok=True)
            backup_path = backup_dir / f"{target.stem}_{timestamp}{target.suffix}"

            try:
                shutil.copy2(target, backup_path)
            except Exception as e:
                return ToolResult(success=False, error=f"Failed to create backup: {e}")

        # Write new content
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write file: {e}")

        return ToolResult(
            success=True,
            data={
                "file": str(target),
                "backup": str(backup_path) if backup_path else None,
                "bytes_written": len(content.encode('utf-8')),
            },
            verification={"file": str(target), "backup": str(backup_path) if backup_path else None}
        )


class SafeEditTool(BaseTool):
    """Safely edit a file with diff preview"""
    name = "safe_edit"
    description = "Edit a file with a search/replace pattern, showing diff preview"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file to edit"),
        ToolParameter(name="old_text", type="string", description="Text to search for"),
        ToolParameter(name="new_text", type="string", description="Text to replace with"),
        ToolParameter(name="preview_only", type="boolean", description="Only show diff, don't apply", required=False, default=False),
    ]

    def execute(self, file_path: str, old_text: str, new_text: str, preview_only: bool = False) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        target = Path(file_path).expanduser().resolve()
        if not workspace_manager.is_path_authorized(ws_id, str(target)):
            return ToolResult(success=False, error="Path not authorized")

        if not target.exists():
            return ToolResult(success=False, error=f"File not found: {target}")

        try:
            with open(target, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")

        if old_text not in content:
            return ToolResult(success=False, error=f"Search text not found in file")

        # Count occurrences
        count = content.count(old_text)
        if count > 1:
            return ToolResult(
                success=False,
                error=f"Search text found {count} times. Please be more specific."
            )

        # Generate new content
        new_content = content.replace(old_text, new_text)

        # Generate diff
        import difflib
        diff = list(difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target),
            lineterm=''
        ))

        diff_text = "\n".join(diff)

        if preview_only:
            return ToolResult(
                success=True,
                data={
                    "file": str(target),
                    "diff": diff_text,
                    "changes": 1,
                },
                verification={"file": str(target), "action": "previewed"}
            )

        # Apply change
        try:
            with open(target, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write file: {e}")

        return ToolResult(
            success=True,
            data={
                "file": str(target),
                "diff": diff_text,
                "changes": 1,
            },
            verification={"file": str(target), "action": "edited"}
        )


class GitStatusTool(BaseTool):
    """Check git status in workspace"""
    name = "git_status"
    description = "Get git status of the workspace"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = []

    def execute(self) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_root = Path(ws.path)

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Also get branch info
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                timeout=5,
            )

            return ToolResult(
                success=True,
                data={
                    "branch": branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown",
                    "status": result.stdout.strip() if result.stdout else "clean",
                    "clean": result.returncode == 0 and not result.stdout.strip(),
                },
                verification={"action": "git_status"}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Git status failed: {e}")


class GitDiffTool(BaseTool):
    """Show git diff"""
    name = "git_diff"
    description = "Show git diff for workspace or specific file"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="file_path", type="string", description="Specific file to diff (optional)", required=False),
        ToolParameter(name="staged", type="boolean", description="Show staged changes", required=False, default=False),
    ]

    def execute(self, file_path: str = None, staged: bool = False) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_root = Path(ws.path)

        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        if file_path:
            target = Path(file_path)
            if not target.is_absolute():
                target = ws_root / target
            if not workspace_manager.is_path_authorized(ws_id, str(target)):
                return ToolResult(success=False, error="Path not authorized")
            cmd.append(str(target))

        try:
            result = subprocess.run(
                cmd,
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                timeout=10,
            )

            return ToolResult(
                success=True,
                data={
                    "diff": result.stdout,
                    "file": file_path,
                    "staged": staged,
                },
                verification={"action": "git_diff"}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Git diff failed: {e}")


class GitCommitTool(BaseTool):
    """Commit changes"""
    name = "git_commit"
    description = "Commit changes with a message"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="message", type="string", description="Commit message"),
        ToolParameter(name="files", type="array", description="Specific files to commit (optional)", required=False),
    ]

    def execute(self, message: str, files: List[str] = None) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_root = Path(ws.path)

        cmd = ["git", "add"]
        if files:
            # Validate each file
            for f in files:
                target = Path(f)
                if not target.is_absolute():
                    target = ws_root / target
                if not workspace_manager.is_path_authorized(ws_id, str(target)):
                    return ToolResult(success=False, error=f"Path not authorized: {f}")
                cmd.append(str(target))
        else:
            cmd.append("-A")

        try:
            subprocess.run(cmd, cwd=str(ws_root), check=True, timeout=10)
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                timeout=10,
            )

            return ToolResult(
                success=result.returncode == 0,
                data={
                    "message": message,
                    "output": result.stdout,
                    "error": result.stderr,
                },
                verification={"action": "git_commit", "message": message}
            )
        except subprocess.CalledProcessError as e:
            return ToolResult(success=False, error=f"Git commit failed: {e.stderr}")
        except Exception as e:
            return ToolResult(success=False, error=f"Git commit failed: {e}")


# Register automation tools
tool_registry.register(ExplainCodeTool())
tool_registry.register(AnalyzeCodeTool())
tool_registry.register(RunTestsTool())
tool_registry.register(GenerateDocsTool())
tool_registry.register(SafeWriteTool())
tool_registry.register(SafeEditTool())
tool_registry.register(GitStatusTool())
tool_registry.register(GitDiffTool())
tool_registry.register(GitCommitTool())