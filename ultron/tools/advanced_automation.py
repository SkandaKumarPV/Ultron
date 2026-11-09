"""ULTRON Advanced Workspace Automation - Refactoring, debugging, code generation, security"""
import os
import subprocess
import ast
import json
import shutil
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from dataclasses import dataclass

from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.security.permissions import PermissionLevel
from ultron.workspace.selector import workspace_manager
from ultron.workspace.analyzer import workspace_analyzer
from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class RenameSymbolTool(BaseTool):
    """Rename a symbol (function, class, variable) across the workspace"""
    name = "rename_symbol"
    description = "Rename a function, class, or variable across all files in workspace"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="old_name", type="string", description="Current symbol name"),
        ToolParameter(name="new_name", type="string", description="New symbol name"),
        ToolParameter(name="symbol_type", type="string", description="Type of symbol", required=False, default="any",
                      enum=["function", "class", "variable", "any"]),
        ToolParameter(name="preview_only", type="boolean", description="Show diff without applying", required=False, default=False),
    ]

    def execute(self, old_name: str, new_name: str, symbol_type: str = "any", preview_only: bool = False) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_root = Path(ws.path)

        # Find all Python files
        py_files = list(ws_root.rglob("*.py"))
        
        changes = []
        for py_file in py_files:
            if not workspace_manager.is_path_authorized(ws_id, str(py_file)):
                continue
            
            try:
                with open(py_file, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception:
                continue

            # Parse AST to find symbol definitions
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            # Find matching definitions
            matches = []
            for node in ast.walk(tree):
                if symbol_type in ("function", "any") and isinstance(node, ast.FunctionDef):
                    if node.name == old_name:
                        matches.append(("function", node, py_file))
                elif symbol_type in ("class", "any") and isinstance(node, ast.ClassDef):
                    if node.name == old_name:
                        matches.append(("class", node, py_file))
                elif symbol_type in ("variable", "any"):
                    # Check assignments
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == old_name:
                                matches.append(("variable", node, py_file))

            if matches:
                # Generate new content
                new_content = content.replace(old_name, new_name)
                if new_content != content:
                    changes.append({
                        "file": str(py_file),
                        "old_content": content,
                        "new_content": new_content,
                    })

        if not changes:
            return ToolResult(success=True, data={"message": f"No occurrences of '{old_name}' found", "changes": 0})

        # Generate unified diffs
        import difflib
        all_diffs = []
        for change in changes:
            diff = list(difflib.unified_diff(
                change["old_content"].splitlines(keepends=True),
                change["new_content"].splitlines(keepends=True),
                fromfile=change["file"],
                tofile=change["file"],
                lineterm=''
            ))
            all_diffs.extend(diff)

        diff_text = "\n".join(all_diffs)

        if preview_only:
            return ToolResult(
                success=True,
                data={"diff": diff_text, "files_affected": len(changes), "changes": len(changes)},
                verification={"action": "preview", "files_affected": len(changes)}
            )

        # Apply changes
        applied = 0
        for change in changes:
            try:
                with open(change["file"], 'w', encoding='utf-8') as f:
                    f.write(change["new_content"])
                applied += 1
            except Exception as e:
                return ToolResult(success=False, error=f"Failed to write {change['file']}: {e}")

        return ToolResult(
            success=True,
            data={"diff": diff_text, "files_affected": applied, "changes": applied},
            verification={"action": "renamed", "old_name": old_name, "new_name": new_name, "files_affected": applied}
        )


class ExtractMethodTool(BaseTool):
    """Extract a code block into a new function"""
    name = "extract_method"
    description = "Extract selected code lines into a new function"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file"),
        ToolParameter(name="start_line", type="integer", description="Starting line number (1-indexed)"),
        ToolParameter(name="end_line", type="integer", description="Ending line number (1-indexed)"),
        ToolParameter(name="new_function_name", type="string", description="Name for the new function"),
        ToolParameter(name="preview_only", type="boolean", description="Show diff without applying", required=False, default=False),
    ]

    def execute(self, file_path: str, start_line: int, end_line: int, new_function_name: str, preview_only: bool = False) -> ToolResult:
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
                lines = f.readlines()
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")

        if start_line < 1 or end_line > len(lines) or start_line > end_line:
            return ToolResult(success=False, error="Invalid line range")

        # Extract the code block
        extracted = "".join(lines[start_line - 1:end_line])
        
        # Determine indentation
        indent = ""
        for line in lines[start_line - 1:end_line]:
            if line.strip():
                indent = line[:len(line) - len(line.lstrip())]
                break

        # Create new function
        func_lines = [
            f"\ndef {new_function_name}():\n",
            "    \"\"\"Extracted method.\"\"\"\n",
        ]
        for line in lines[start_line - 1:end_line]:
            func_lines.append("    " + line)
        func_lines.append("\n")

        new_function = "".join(func_lines)

        # Replace the block with function call
        new_lines = lines[:start_line - 1]
        new_lines.append(f"{indent}{new_function_name}()\n")
        new_lines.extend(lines[end_line:])

        new_content = "".join(new_lines) + new_function

        # Generate diff
        import difflib
        diff = list(difflib.unified_diff(
            "".join(lines).splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            lineterm=''
        ))
        diff_text = "\n".join(diff)

        if preview_only:
            return ToolResult(
                success=True,
                data={"diff": diff_text},
                verification={"action": "preview"}
            )

        # Apply
        try:
            with open(target, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write file: {e}")

        return ToolResult(
            success=True,
            data={"diff": diff_text},
            verification={"action": "extracted", "function": new_function_name}
        )


class InlineVariableTool(BaseTool):
    """Inline a variable (replace usage with its value)"""
    name = "inline_variable"
    description = "Replace variable usages with its assigned value"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file"),
        ToolParameter(name="variable_name", type="string", description="Variable to inline"),
        ToolParameter(name="preview_only", type="boolean", description="Show diff without applying", required=False, default=False),
    ]

    def execute(self, file_path: str, variable_name: str, preview_only: bool = False) -> ToolResult:
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

        # Find assignment using AST
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return ToolResult(success=False, error="Syntax error in file")

        assigned_value = None
        assign_lineno = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target_node in node.targets:
                    if isinstance(target_node, ast.Name) and target_node.id == variable_name:
                        if isinstance(node.value, (ast.Constant, ast.Str, ast.Num, ast.List, ast.Dict, ast.Tuple)):
                            assigned_value = ast.get_source_segment(content, node.value)
                            assign_lineno = node.lineno
                        break

        if not assigned_value:
            return ToolResult(success=False, error=f"Variable '{variable_name}' not found or not a simple literal")

        # Replace usages AFTER the assignment line
        lines = content.splitlines(keepends=True)
        new_lines = []
        for i, line in enumerate(lines, 1):
            if i >= assign_lineno:
                # Replace variable usage in this line (but not in the assignment itself)
                import re
                pattern = r'\b' + re.escape(variable_name) + r'\b'
                # Don't replace on the assignment line itself
                if i != assign_lineno:
                    line = re.sub(r'\b' + re.escape(variable_name) + r'\b', assigned_value, line)
                new_lines.append(line)
            else:
                new_lines.append(line)

        new_content = "".join(new_lines)

        if new_content == content:
            return ToolResult(success=False, error=f"No usages of '{variable_name}' found to inline after assignment")

        # Generate diff
        import difflib
        diff = list(difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            lineterm=''
        ))
        diff_text = "\n".join(diff)

        if preview_only:
            return ToolResult(
                success=True,
                data={"diff": diff_text, "value": assigned_value},
                verification={"action": "preview"}
            )

        # Apply
        try:
            with open(target, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write file: {e}")

        return ToolResult(
            success=True,
            data={"diff": diff_text, "value": assigned_value},
            verification={"action": "inlined", "variable": variable_name}
        )


class GenerateTestsTool(BaseTool):
    """Generate unit tests for a function or class"""
    name = "generate_tests"
    description = "Generate unit tests for a function or class using RAG"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.CREATE
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file containing code to test"),
        ToolParameter(name="target_name", type="string", description="Function or class name to generate tests for"),
        ToolParameter(name="test_framework", type="string", description="Test framework", required=False, default="pytest",
                      enum=["pytest", "unittest"]),
        ToolParameter(name="output_path", type="string", description="Output test file path (optional)", required=False),
    ]

    def execute(self, file_path: str, target_name: str, test_framework: str = "pytest", output_path: str = None) -> ToolResult:
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

        # Find the target in AST
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ToolResult(success=False, error="Syntax error in file")

        target_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == target_name:
                target_node = node
                break

        if not target_node:
            return ToolResult(success=False, error=f"Target '{target_name}' not found")

        # Get source code of target
        target_code = ast.get_source_segment(code, target_node) or ""

        # Query RAG for test generation
        question = f"""Generate {test_framework} unit tests for the following {type(target_node).__name__.lower()}:
        
{target_code}

Include: edge cases, normal cases, error cases, and mocking where appropriate.
Follow {test_framework} best practices."""

        result = workspace_analyzer.query(ws_id, question)

        # Determine output path
        if output_path:
            out = Path(output_path).expanduser().resolve()
        else:
            out = target.parent / f"test_{target.stem}.py"

        # Write test file
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, 'w', encoding='utf-8') as f:
                f.write(result.answer)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write test file: {e}")

        return ToolResult(
            success=True,
            data={
                "test_file": str(out),
                "target": target_name,
                "framework": test_framework,
                "preview": result.answer[:500],
            },
            verification={"action": "generated_tests", "target": target_name, "output": str(out)}
        )


class DependencyGraphTool(BaseTool):
    """Analyze and visualize import dependencies"""
    name = "dependency_graph"
    description = "Analyze import dependencies between files in workspace"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="format", type="string", description="Output format", required=False, default="json",
                      enum=["json", "dot", "text"]),
        ToolParameter(name="include_external", type="boolean", description="Include external (pip) dependencies", required=False, default=False),
    ]

    def execute(self, format: str = "json", include_external: bool = False) -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_root = Path(ws.path)

        py_files = list(ws_root.rglob("*.py"))
        
        graph = {"nodes": [], "edges": []}
        file_imports = {}
        file_to_module = {}

        # Map files to module names
        for py_file in py_files:
            rel = py_file.relative_to(ws_root)
            module = str(rel.with_suffix("")).replace("\\", ".").replace("/", ".")
            file_to_module[str(py_file)] = module
            graph["nodes"].append({"id": module, "file": str(py_file.relative_to(ws_root))})

        # Parse imports
        for py_file in py_files:
            if not workspace_manager.is_path_authorized(ws_id, str(py_file)):
                continue
            
            try:
                with open(py_file, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception:
                continue

            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            source_module = file_to_module[str(py_file)]
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = alias.name
                        if include_external or target in file_to_module.values():
                            graph["edges"].append({"from": source_module, "to": target, "type": "import"})
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        target = node.module
                        if node.level > 0:  # Relative import
                            # Resolve relative
                            parts = source_module.split(".")
                            target = ".".join(parts[:-node.level]) + (("." + target) if target else "")
                        if include_external or target in file_to_module.values():
                            graph["edges"].append({"from": source_module, "to": target, "type": "from_import"})

        # Format output
        if format == "dot":
            output = "digraph dependencies {\n"
            for edge in graph["edges"]:
                output += f'  "{edge["from"]}" -> "{edge["to"]}";\n'
            output += "}"
            return ToolResult(success=True, data={"graph": output}, verification={"action": "dependency_graph"})
        elif format == "text":
            lines = []
            for edge in graph["edges"]:
                lines.append(f"{edge['from']} -> {edge['to']} ({edge['type']})")
            return ToolResult(success=True, data={"graph": "\n".join(lines)}, verification={"action": "dependency_graph"})
        else:
            return ToolResult(success=True, data=graph, verification={"action": "dependency_graph"})


class SecurityScanTool(BaseTool):
    """Scan for security issues (secrets, vulnerabilities, etc.)"""
    name = "security_scan"
    description = "Scan workspace for security issues: secrets, vulnerabilities, license issues"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="scan_type", type="string", description="Type of scan", required=False, default="all",
                      enum=["secrets", "vulnerabilities", "licenses", "all"]),
    ]

    def execute(self, scan_type: str = "all") -> ToolResult:
        ws_id = workspace_manager.get_current_workspace_id()
        if not ws_id:
            return ToolResult(success=False, error="No workspace selected")

        ws = workspace_manager.get_workspace(ws_id)
        ws_root = Path(ws.path)

        issues = []

        # Secret patterns
        secret_patterns = {
            "api_key": r'(?i)(api[_-]?key|apikey)\s*[=:]\s*["\']?[a-zA-Z0-9_\-]{20,}["\']?',
            "secret": r'(?i)(secret[_-]?key|secret)\s*[=:]\s*["\']?[a-zA-Z0-9_\-]{20,}["\']?',
            "password": r'(?i)(password|passwd)\s*[=:]\s*["\']?[^\s"\']{8,}["\']?',
            "token": r'(?i)(token|auth[_-]?token)\s*[=:]\s*["\']?[a-zA-Z0-9_\-\.]{20,}["\']?',
            "aws_key": r'AKIA[0-9A-Z]{16}',
            "private_key": r'-----BEGIN (RSA|DSA|EC|OPENSSH) PRIVATE KEY-----',
        }

        py_files = list(ws_root.rglob("*.py"))
        py_files.extend(ws_root.rglob("*.txt"))
        py_files.extend(ws_root.rglob("*.json"))
        py_files.extend(ws_root.rglob("*.yaml"))
        py_files.extend(ws_root.rglob("*.yml"))
        py_files.extend(ws_root.rglob(".env*"))

        import re
        for f in py_files:
            if not workspace_manager.is_path_authorized(ws_id, str(f)):
                continue
            
            try:
                with open(f, 'r', encoding='utf-8', errors='replace') as fp:
                    content = fp.read()
            except Exception:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                for name, pattern in secret_patterns.items():
                    if re.search(pattern, line):
                        issues.append({
                            "type": "secret",
                            "subtype": name,
                            "file": str(f.relative_to(ws_root)),
                            "line": i,
                            "severity": "high",
                            "preview": line[:100],
                        })

        # TODO: Add vulnerability scanning (requirements.txt, package.json)
        # TODO: Add license scanning

        return ToolResult(
            success=True,
            data={
                "issues": issues,
                "count": len(issues),
                "scan_type": scan_type,
            },
            verification={"action": "security_scan", "issues_found": len(issues)}
        )


class DebugAssistantTool(BaseTool):
    """Debug assistant - set breakpoints, inspect variables"""
    name = "debug_assistant"
    description = "Debug assistant: analyze code for debugging, suggest breakpoints"
    category = ToolCategory.WORKSPACE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to file to analyze"),
        ToolParameter(name="action", type="string", description="Debug action", 
                      enum=["suggest_breakpoints", "analyze_flow", "find_bugs"]),
    ]

    def execute(self, file_path: str, action: str = "suggest_breakpoints") -> ToolResult:
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

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return ToolResult(success=False, error=f"Syntax error: {e}")

        results = {"file": file_path, "action": action}

        if action == "suggest_breakpoints":
            breakpoints = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    # Entry point
                    breakpoints.append({
                        "line": node.lineno,
                        "type": "entry",
                        "name": node.name,
                        "reason": f"Entry to {type(node).__name__.lower()} {node.name}"
                    })
                elif isinstance(node, (ast.Raise, ast.Return)):
                    # Exception points
                    breakpoints.append({
                        "line": node.lineno,
                        "type": "exit",
                        "reason": f"{type(node).__name__.lower()} statement"
                    })
                elif isinstance(node, (ast.Call,)):
                    # Function calls - potential side effects
                    if isinstance(node.func, ast.Name) and node.func.id in ('print', 'input', 'open', 'write', 'execute', 'commit', 'send'):
                        breakpoints.append({
                            "line": node.lineno,
                            "type": "side_effect",
                            "reason": f"Side effect call: {node.func.id}"
                        })
            results["breakpoints"] = breakpoints[:20]  # Limit

        elif action == "analyze_flow":
            # Control flow analysis
            results["functions"] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    func_info = {
                        "name": node.name,
                        "line": node.lineno,
                        "args": [a.arg for a in node.args.args],
                        "returns": bool([n for n in ast.walk(node) if isinstance(n, ast.Return)]),
                        "raises": [n for n in ast.walk(node) if isinstance(n, ast.Raise)],
                        "loops": [n for n in ast.walk(node) if isinstance(n, (ast.For, ast.While))],
                        "conditionals": [n for n in ast.walk(node) if isinstance(n, ast.If)],
                    }
                    results["functions"].append(func_info)

        elif action == "find_bugs":
            # Common bug patterns
            bugs = []
            for node in ast.walk(tree):
                # Mutable default argument
                if isinstance(node, ast.FunctionDef):
                    for default in node.args.defaults:
                        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                            bugs.append({
                                "line": node.lineno,
                                "type": "mutable_default",
                                "message": f"Function '{node.name}' has mutable default argument",
                                "severity": "medium"
                            })
                # Bare except
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    bugs.append({
                        "line": node.lineno,
                        "type": "bare_except",
                        "message": "Bare except: catches all exceptions including KeyboardInterrupt",
                        "severity": "medium"
                    })
                # Unused variable
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.startswith('_'):
                            pass  # Convention for unused
            results["bugs"] = bugs

        return ToolResult(
            success=True,
            data=results,
            verification={"action": action, "file": file_path}
        )


# Register advanced automation tools
tool_registry.register(RenameSymbolTool())
tool_registry.register(ExtractMethodTool())
tool_registry.register(InlineVariableTool())
tool_registry.register(GenerateTestsTool())
tool_registry.register(DependencyGraphTool())
tool_registry.register(SecurityScanTool())
tool_registry.register(DebugAssistantTool())