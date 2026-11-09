"""ULTRON PC Control Tools - Application launching, file operations"""
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List

from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.security.permissions import PermissionLevel
from ultron.config import config
from ultron.workspace.selector import workspace_manager
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class LaunchAppTool(BaseTool):
    """Launch an allowed application"""
    name = "launch_app"
    description = "Launch an authorized application by name"
    category = ToolCategory.PC_CONTROL
    permission_level = PermissionLevel.EXECUTE
    parameters = [
        ToolParameter(name="app_name", type="string", description="Application name (e.g., notepad, code, explorer)"),
        ToolParameter(name="args", type="array", description="Command line arguments", required=False, default=[]),
    ]

    def execute(self, app_name: str, args: List[str] = None) -> ToolResult:
        args = args or []
        cfg = config.config.pc_control
        app_name = self._canonical_app_name(app_name)

        # Check if app is allowed
        allowed = False
        for allowed_app in cfg.allowed_apps:
            if app_name.lower() in allowed_app.lower() or allowed_app.lower() in app_name.lower():
                allowed = True
                break

        if not allowed:
            return ToolResult(
                success=False,
                error=f"Application '{app_name}' not in allowed list: {cfg.allowed_apps}"
            )

        try:
            # First, try to find the actual executable path for known apps
            resolved_app = self._resolve_app_path(app_name)
            
            # Normalize app name for comparison (strip .exe)
            app_name_lower = app_name.lower()
            if app_name_lower.endswith('.exe'):
                app_name_base = app_name_lower[:-4]
            else:
                app_name_base = app_name_lower
            
            # For Windows GUI apps, use os.startfile which is more reliable
            if app_name_base in ['notepad', 'explorer', 'chrome', 'firefox', 'edge', 'code']:
                if resolved_app:
                    import os
                    os.startfile(resolved_app)
                else:
                    import os
                    os.startfile(app_name_base + '.exe')
                # Give it a moment to start
                import time
                time.sleep(0.5)
                return ToolResult(
                    success=True,
                    data=f"Launched {app_name_base}",
                    verification={"app": app_name_base, "method": "startfile"}
                )
            
            # For console apps, use subprocess
            if resolved_app:
                cmd = [resolved_app] + args
            elif app_name.lower() in ['cmd', 'powershell', 'wt']:
                cmd = [app_name] + args
            else:
                cmd = [app_name] + args
            
            proc = subprocess.Popen(cmd, start_new_session=True)

            # Give it a moment to start
            import time
            time.sleep(0.5)

            # Verify process is running
            if proc.poll() is None:
                return ToolResult(
                    success=True,
                    data=f"Launched {app_name} (PID: {proc.pid})",
                    verification={"pid": proc.pid, "app": app_name}
                )
            else:
                return ToolResult(
                    success=False,
                    error=f"Process exited immediately with code {proc.returncode}"
                )

        except FileNotFoundError:
            return ToolResult(success=False, error=f"Application not found: {app_name}")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to launch: {e}")

    def _resolve_app_path(self, app_name: str) -> Optional[str]:
        """Resolve common application names to their full paths"""
        import os
        import glob
        
        app_name_lower = app_name.lower()
        
        # Common installation paths for known apps
        common_paths = {
            'chrome.exe': [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ],
            'firefox.exe': [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            ],
            'edge.exe': [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ],
            'code.exe': [
                r"C:\Program Files\Microsoft VS Code\Code.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
            ],
        }
        
        if app_name_lower in common_paths:
            for path in common_paths[app_name_lower]:
                if os.path.exists(path):
                    return path
        
        # Try to find in PATH
        path_dirs = os.environ.get('PATH', '').split(os.pathsep)
        for path_dir in path_dirs:
            full_path = os.path.join(path_dir, app_name)
            if os.path.exists(full_path):
                return full_path
        
        return None

    @staticmethod
    def _canonical_app_name(app_name: str) -> str:
        aliases = {
            "google chrome": "chrome.exe",
            "chrome": "chrome.exe",
            "mozilla firefox": "firefox.exe",
            "firefox": "firefox.exe",
            "microsoft edge": "msedge.exe",
            "edge": "msedge.exe",
            "visual studio code": "code.exe",
            "vs code": "code.exe",
            "vscode": "code.exe",
        }
        name = app_name.strip().lower()
        return aliases.get(name, app_name)


class OpenFileTool(BaseTool):
    """Open a file with its default application"""
    name = "open_file"
    description = "Open a file with its default application (within authorized paths)"
    category = ToolCategory.PC_CONTROL
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file to open"),
    ]

    def execute(self, file_path: str) -> ToolResult:
        path = Path(file_path).expanduser().resolve()

        # Check workspace authorization
        ws_id = workspace_manager.get_current_workspace_id()
        if ws_id and not workspace_manager.is_path_authorized(ws_id, str(path)):
            return ToolResult(
                success=False,
                error=f"Path not authorized in current workspace: {path}"
            )

        # Check allowed paths
        cfg = config.config.pc_control
        allowed = False
        for allowed_path in cfg.allowed_paths:
            try:
                if path.is_relative_to(Path(allowed_path).expanduser().resolve()):
                    allowed = True
                    break
            except Exception:
                pass

        if not allowed:
            return ToolResult(
                success=False,
                error=f"Path not in allowed directories: {path}"
            )

        if not path.exists():
            return ToolResult(success=False, error=f"File not found: {path}")

        try:
            os.startfile(str(path))
            return ToolResult(
                success=True,
                data=f"Opened {path}",
                verification={"path": str(path), "exists": True}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to open file: {e}")


class OpenFolderTool(BaseTool):
    """Open a folder in Explorer"""
    name = "open_folder"
    description = "Open a folder in Windows Explorer (within authorized paths)"
    category = ToolCategory.PC_CONTROL
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="folder_path", type="string", description="Path to folder to open"),
    ]

    def execute(self, folder_path: str) -> ToolResult:
        path = Path(folder_path).expanduser().resolve()

        # Check workspace authorization
        ws_id = workspace_manager.get_current_workspace_id()
        if ws_id and not workspace_manager.is_path_authorized(ws_id, str(path)):
            return ToolResult(
                success=False,
                error=f"Path not authorized in current workspace: {path}"
            )

        # Check allowed paths
        cfg = config.config.pc_control
        allowed = False
        for allowed_path in cfg.allowed_paths:
            try:
                if path.is_relative_to(Path(allowed_path).expanduser().resolve()):
                    allowed = True
                    break
            except Exception:
                pass

        if not allowed:
            return ToolResult(
                success=False,
                error=f"Path not in allowed directories: {path}"
            )

        if not path.exists():
            return ToolResult(success=False, error=f"Folder not found: {path}")

        if not path.is_dir():
            return ToolResult(success=False, error=f"Not a directory: {path}")

        try:
            os.startfile(str(path))
            return ToolResult(
                success=True,
                data=f"Opened folder {path}",
                verification={"path": str(path), "exists": True, "is_dir": True}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to open folder: {e}")


class ListDirectoryTool(BaseTool):
    """List contents of a directory"""
    name = "list_directory"
    description = "List files and folders in a directory (within authorized paths)"
    category = ToolCategory.PC_CONTROL
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="path", type="string", description="Directory path", required=False, default="."),
        ToolParameter(name="recursive", type="boolean", description="List recursively", required=False, default=False),
        ToolParameter(name="pattern", type="string", description="Glob pattern filter", required=False, default="*"),
    ]

    def execute(self, path: str = ".", recursive: bool = False, pattern: str = "*") -> ToolResult:
        target = Path(path).expanduser().resolve()

        # Check workspace authorization
        ws_id = workspace_manager.get_current_workspace_id()
        if ws_id and not workspace_manager.is_path_authorized(ws_id, str(target)):
            return ToolResult(
                success=False,
                error=f"Path not authorized in current workspace: {target}"
            )

        if not target.exists():
            return ToolResult(success=False, error=f"Path not found: {target}")

        if not target.is_dir():
            return ToolResult(success=False, error=f"Not a directory: {target}")

        try:
            if recursive:
                items = list(target.rglob(pattern))
            else:
                items = list(target.glob(pattern))

            results = []
            for item in items:
                try:
                    stat = item.stat()
                    results.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "size": stat.st_size if item.is_file() else 0,
                        "modified": stat.st_mtime,
                    })
                except Exception:
                    results.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "size": 0,
                        "modified": 0,
                    })

            return ToolResult(
                success=True,
                data={"items": results, "count": len(results)},
                verification={"path": str(target), "count": len(results)}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to list directory: {e}")


class RunCommandTool(BaseTool):
    """Run a command in shell (restricted to allowed commands)"""
    name = "run_command"
    description = "Run a shell command (restricted to allowed commands)"
    category = ToolCategory.PC_CONTROL
    permission_level = PermissionLevel.EXECUTE
    parameters = [
        ToolParameter(name="command", type="string", description="Command to run"),
        ToolParameter(name="cwd", type="string", description="Working directory", required=False, default="."),
        ToolParameter(name="timeout", type="integer", description="Timeout in seconds", required=False, default=30),
    ]

    # Allowed commands (base commands only)
    ALLOWED_COMMANDS = {
        "python", "python3", "pip", "npm", "node", "git", "dir", "ls",
        "type", "cat", "echo", "pwd", "cd", "mkdir", "rmdir",
        "dotnet", "cargo", "go", "java", "javac",
    }

    def execute(self, command: str, cwd: str = ".", timeout: int = 30) -> ToolResult:
        # Parse command
        parts = command.strip().split()
        if not parts:
            return ToolResult(success=False, error="Empty command")

        base_cmd = parts[0].lower()
        if base_cmd not in self.ALLOWED_COMMANDS:
            return ToolResult(
                success=False,
                error=f"Command not allowed: {base_cmd}. Allowed: {', '.join(sorted(self.ALLOWED_COMMANDS))}"
            )

        # Resolve working directory
        work_dir = Path(cwd).expanduser().resolve()

        # Check workspace authorization
        ws_id = workspace_manager.get_current_workspace_id()
        if ws_id and not workspace_manager.is_path_authorized(ws_id, str(work_dir)):
            return ToolResult(
                success=False,
                error=f"Working directory not authorized: {work_dir}"
            )

        try:
            result = subprocess.run(
                parts,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )

            return ToolResult(
                success=result.returncode == 0,
                data={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                },
                verification={"command": command, "cwd": str(work_dir), "returncode": result.returncode}
            )

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, error=f"Command failed: {e}")


# Register tools
tool_registry.register(LaunchAppTool())
tool_registry.register(OpenFileTool())
tool_registry.register(OpenFolderTool())
tool_registry.register(ListDirectoryTool())
tool_registry.register(RunCommandTool())