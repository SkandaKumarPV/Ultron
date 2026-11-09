"""ULTRON Kernel - Central Router and Orchestrator"""
from typing import Optional, Dict, Any, List, AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import time
import threading
import uuid

from ultron.config import config
from ultron.tools.registry import tool_registry, ToolResult, ToolCategory
from ultron.security.permissions import permission_registry, PermissionLevel
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity
from ultron.model.loader import ModelManager


class RequestType(Enum):
    VOICE_COMMAND = "voice_command"
    TEXT_COMMAND = "text_command"
    WORKSPACE_QUERY = "workspace_query"
    PC_ACTION = "pc_action"
    MODEL_INFERENCE = "model_inference"
    SYSTEM = "system"


@dataclass
class UltronRequest:
    request_id: str
    request_type: RequestType
    input_text: str
    workspace_id: Optional[str] = None
    user_id: str = "local_user"
    context: Dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    voice_mode: bool = False


@dataclass
class UltronResponse:
    request_id: str
    success: bool
    response_text: str
    spoken_response: Optional[str] = None  # For TTS
    tool_results: List[ToolResult] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class Kernel:
    """Central ULTRON kernel - routes requests to appropriate handlers"""

    def __init__(self):
        self._model_manager: Optional[ModelManager] = None
        self._workspace_manager = None  # Initialized later
        self._voice_pipeline = None     # Initialized later
        self._running = False
        self._lock = threading.RLock()
        self._request_count = 0

    def initialize(self, start_voice: bool = True):
        """Initialize kernel components"""
        with self._lock:
            if self._running:
                return

            # Load configuration
            cfg = config.config

            # Import tools to register them
            import ultron.tools

            # Initialize model manager
            from ultron.model.loader import ModelManager
            self._model_manager = ModelManager()
            self._model_manager.initialize()

            # Initialize workspace manager
            from ultron.workspace.selector import WorkspaceManager
            self._workspace_manager = WorkspaceManager()

            # Initialize voice pipeline (lazy - only when enabled)
            if cfg.voice.enabled and start_voice:
                from ultron.voice.pipeline import voice_pipeline
                voice_pipeline.initialize()
                
                # Start voice pipeline with callback to route voice commands
                def voice_callback(text: str):
                    # Process voice input through kernel
                    from ultron.kernel import UltronRequest, RequestType
                    import time
                    request = UltronRequest(
                        request_id=f"req_{int(time.time()*1000)}",
                        request_type=RequestType.VOICE_COMMAND,
                        input_text=text,
                        workspace_id=self._workspace_manager.get_current_workspace_id() if self._workspace_manager else None,
                        voice_mode=True,
                    )
                    # Process asynchronously
                    import asyncio
                    asyncio.create_task(self.process_async(request))
                
                voice_pipeline.start(voice_callback)
                self._voice_pipeline = voice_pipeline

            self._running = True
            audit_log(AuditEventType.MODEL_LOADED, severity=AuditSeverity.INFO,
                      details={"status": "kernel_initialized"})

    def shutdown(self):
        """Shutdown kernel gracefully"""
        with self._lock:
            if not self._running:
                return

            # Unload model
            if self._model_manager:
                self._model_manager.unload()

            # Stop voice pipeline
            if self._voice_pipeline:
                self._voice_pipeline.stop()

            self._running = False
            audit_log(AuditEventType.MODEL_UNLOADED, severity=AuditSeverity.INFO,
                      details={"status": "kernel_shutdown"})

    def process(self, request: UltronRequest) -> UltronResponse:
        """Process a request synchronously"""
        return asyncio.run(self.process_async(request))

    async def process_async(self, request: UltronRequest) -> UltronResponse:
        """Process a request asynchronously"""
        print(f"[DEBUG] process_async called, _start_time will be set")
        _start_time = time.time()
        print(f"[DEBUG] _start_time = {_start_time}")
        
        with self._lock:
            self._request_count += 1

        # Audit: request received
        audit_log(
            AuditEventType.TOOL_INVOKED if request.request_type != RequestType.MODEL_INFERENCE
            else AuditEventType.MODEL_INFERENCE,
            workspace_id=request.workspace_id,
            tool_name=f"kernel.{request.request_type.value}",
            action="process",
            target=request.input_text[:100],
            details={"request_type": request.request_type.value, "voice_mode": request.voice_mode},
        )

        try:
            # Route request
            print("[DEBUG] About to call _route_request")
            response = await self._route_request(request)
            print(f"[DEBUG] _route_request returned: {response.success}")

            # Add metadata
            response.metadata["processing_time_ms"] = (time.time() - _start_time) * 1000
            response.metadata["request_count"] = self._request_count

            # Audit: response
            audit_log(
                AuditEventType.TOOL_RESULT,
                workspace_id=request.workspace_id,
                tool_name=f"kernel.{request.request_type.value}",
                result="success" if response.success else "failure",
                details={"processing_time_ms": response.metadata["processing_time_ms"]},
            )

            return response

        except Exception as e:
            import traceback
            print(f"[DEBUG] Exception in process_async: {e}")
            traceback.print_exc()
            error_response = UltronResponse(
                request_id=request.request_id,
                success=False,
                response_text=f"Error processing request: {str(e)}",
                error=str(e),
            )
            audit_log(
                AuditEventType.ERROR,
                workspace_id=request.workspace_id,
                severity=AuditSeverity.ERROR,
                details={"error": str(e), "request_type": request.request_type.value},
            )
            return error_response

    async def _route_request(self, request: UltronRequest) -> UltronResponse:
        """Route request to appropriate handler"""
        if request.request_type == RequestType.VOICE_COMMAND:
            return await self._handle_voice_command(request)
        elif request.request_type == RequestType.TEXT_COMMAND:
            return await self._handle_text_command(request)
        elif request.request_type == RequestType.WORKSPACE_QUERY:
            return await self._handle_workspace_query(request)
        elif request.request_type == RequestType.PC_ACTION:
            return await self._handle_pc_action(request)
        elif request.request_type == RequestType.MODEL_INFERENCE:
            return await self._handle_model_inference(request)
        elif request.request_type == RequestType.SYSTEM:
            return await self._handle_system(request)
        else:
            return UltronResponse(
                request_id=request.request_id,
                success=False,
                response_text=f"Unknown request type: {request.request_type}",
                error="Unknown request type",
            )

    async def _handle_voice_command(self, request: UltronRequest) -> UltronResponse:
        """Handle voice command (STT already done, text provided)"""
        # Voice commands go through same pipeline as text but with voice_mode=True
        return await self._handle_text_command(request)

    async def _handle_text_command(self, request: UltronRequest) -> UltronResponse:
        """Handle text command - parse intent, execute tools, respond"""
        # Check if it's a direct tool command
        tool_result = await self._try_direct_tool_execution(request)
        if tool_result:
            return tool_result

        # Otherwise, use LLM to understand and respond
        return await self._handle_model_inference(request)

    async def _try_direct_tool_execution(self, request: UltronRequest) -> Optional[UltronResponse]:
        """Try to match request to a direct tool call"""
        # Simple keyword-based routing for common commands
        text = request.input_text.lower()

        # App launching - check for "open/launch/start <app>" pattern first
        import re
        app_match = re.search(r'(open|launch|start)\s+(\w+)', text)
        if app_match:
            action, app_name = app_match.groups()
            # Try launch_app tool
            tool = tool_registry.get("launch_app")
            if tool:
                args = {"app_name": app_name + ".exe" if not app_name.endswith(".exe") else app_name}
                result = await tool_registry.execute_async(
                    tool.name, workspace_id=request.workspace_id, **args
                )
                return UltronResponse(
                    request_id=request.request_id,
                    success=result.success,
                    response_text=self._format_tool_response(result),
                    tool_results=[result],
                )

        # File/folder opening
        if "open " in text:
            for tool in tool_registry.get_by_category(ToolCategory.PC_CONTROL):
                if tool.name in ("open_file", "open_folder"):
                    if tool.name in text or any(kw in text for kw in tool.name.split("_")):
                        result = await tool_registry.execute_async(
                            tool.name, workspace_id=request.workspace_id, **self._extract_tool_args(text, tool)
                        )
                        return UltronResponse(
                            request_id=request.request_id,
                            success=result.success,
                            response_text=self._format_tool_response(result),
                            tool_results=[result],
                        )

        return None

    def _extract_tool_args(self, text: str, tool) -> Dict:
        """Extract tool arguments from text (simplified)"""
        args = {}
        for param in tool.parameters:
            if param.name == "path" or param.name == "file_path":
                # Try to extract path
                import re
                paths = re.findall(r'[A-Za-z]:[\\/][^\s]*|[~/][^\s]*', text)
                if paths:
                    args[param.name] = paths[0]
            elif param.name == "app_name":
                # Extract app name after "open"
                import re
                match = re.search(r'open\s+(\w+)', text)
                if match:
                    args[param.name] = match.group(1) + ".exe"
        return args

    def _format_tool_response(self, result: ToolResult) -> str:
        """Format tool result for user"""
        if result.success:
            if result.data:
                return str(result.data)
            return "Done."
        return f"Failed: {result.error}"

    async def _handle_workspace_query(self, request: UltronRequest) -> UltronResponse:
        """Handle workspace query with retrieval + LLM"""
        if not self._workspace_manager:
            return UltronResponse(
                request_id=request.request_id,
                success=False,
                response_text="Workspace manager not initialized",
                error="Workspace manager not initialized",
            )

        if not request.workspace_id:
            return UltronResponse(
                request_id=request.request_id,
                success=False,
                response_text="No workspace is selected. Select and index a folder first.",
                error="No workspace selected",
            )

        workspace = self._workspace_manager.get_workspace(request.workspace_id)
        if not workspace or workspace.index_status != "ready":
            status = workspace.index_status if workspace else "not found"
            return UltronResponse(
                request_id=request.request_id,
                success=False,
                response_text=f"Workspace is not ready for questions (status: {status}). Index it first.",
                error=f"Workspace not ready: {status}",
            )

        # Get relevant context from the indexed workspace.
        from ultron.workspace.analyzer import workspace_analyzer
        result = await workspace_analyzer.query_async(
            request.workspace_id,
            request.input_text,
        )
        context = result.sources

        response_text = result.answer

        return UltronResponse(
            request_id=request.request_id,
            success=True,
            response_text=response_text,
            metadata={"context_sources": [c.get("source", "") for c in context]},
        )

    async def _handle_pc_action(self, request: UltronRequest) -> UltronResponse:
        """Handle PC control action"""
        # Similar to direct tool execution but for PC actions
        return await self._handle_text_command(request)

    async def _handle_model_inference(self, request: UltronRequest) -> UltronResponse:
        """Handle direct model inference using chat completion"""
        if not self._model_manager:
            return UltronResponse(
                request_id=request.request_id,
                success=False,
                response_text="Model manager not initialized.",
                error="Model manager not initialized",
            )

        # Auto-load model if not loaded
        if not self._model_manager.is_loaded():
            if not self._model_manager.load():
                return UltronResponse(
                    request_id=request.request_id,
                    success=False,
                    response_text="Failed to load model.",
                    error="Model load failed",
                )

        # Use chat completion for Qwen
        messages = [
            {"role": "system", "content": "You are ULTRON, a helpful AI assistant. Answer concisely."},
            {"role": "user", "content": request.input_text},
        ]

        response_text = self._model_manager.chat_completion(
            messages,
            max_tokens=request.context.get("max_tokens", 128),
            temperature=request.context.get("temperature", 0.3),
        )

        return UltronResponse(
            request_id=request.request_id,
            success=True,
            response_text=response_text,
            metadata={"model": self._model_manager.get_model_info()},
        )

    async def _handle_system(self, request: UltronRequest) -> UltronResponse:
        """Handle system commands"""
        text = request.input_text.lower()

        if "status" in text or "health" in text:
            status = self.get_status()
            return UltronResponse(
                request_id=request.request_id,
                success=True,
                response_text=f"ULTRON Status:\n{status}",
            )

        elif "shutdown" in text or "exit" in text:
            self.shutdown()
            return UltronResponse(
                request_id=request.request_id,
                success=True,
                response_text="Shutting down ULTRON...",
            )

        elif "reload" in text or "restart" in text:
            self.shutdown()
            self.initialize()
            return UltronResponse(
                request_id=request.request_id,
                success=True,
                response_text="ULTRON restarted.",
            )

        return UltronResponse(
            request_id=request.request_id,
            success=False,
            response_text=f"Unknown system command: {request.input_text}",
            error="Unknown system command",
        )

    def _build_workspace_prompt(self, query: str, context: List[Dict]) -> str:
        """Build prompt with workspace context"""
        if not context:
            return f"Question: {query}\n\nAnswer based on your knowledge:"

        context_str = "\n\n".join([
            f"[Source: {c.get('source', 'unknown')}]\n{c.get('content', '')}"
            for c in context
        ])

        return f"""You are ULTRON, a helpful AI assistant. Answer the question using ONLY the provided context.
If the answer is not in the context, say "I don't have enough information from the provided documents."

Context:
{context_str}

Question: {query}

Answer:"""

    def get_status(self) -> str:
        """Get kernel status"""
        model_info = "Not loaded"
        if self._model_manager and self._model_manager.is_loaded():
            info = self._model_manager.get_model_info()
            model_info = f"{info.get('name', 'Unknown')} (VRAM: {info.get('vram_used_mb', 0)} MB)"

        workspace_info = "None"
        if self._workspace_manager:
            ws = self._workspace_manager.get_current_workspace()
            workspace_info = ws if ws else "None"

        return f"""Model: {model_info}
Workspace: {workspace_info}
Voice: {"Enabled" if config.config.voice.enabled else "Disabled"}
Requests Processed: {self._request_count}
Tools Registered: {len(tool_registry.get_all())}"""

    def get_model_manager(self) -> Optional[ModelManager]:
        return self._model_manager

    def get_workspace_manager(self):
        return self._workspace_manager

    def get_voice_pipeline(self):
        return self._voice_pipeline


# Global instance
kernel = Kernel()