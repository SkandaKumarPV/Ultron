"""ULTRON Voice Tools - Voice control via tool registry"""
from typing import Optional

from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.security.permissions import PermissionLevel
from ultron.voice.pipeline import voice_pipeline
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class VoiceListenTool(BaseTool):
    """Start listening for voice input (push-to-talk)"""
    name = "voice_listen"
    description = "Start voice recording (push-to-talk). Call again to stop and get transcription."
    category = ToolCategory.VOICE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="action", type="string", description="Action: 'start' or 'stop'", enum=["start", "stop"]),
    ]

    def execute(self, action: str) -> ToolResult:
        if action == "start":
            if voice_pipeline.push_to_talk_start():
                return ToolResult(
                    success=True,
                    data="Listening... Call voice_listen with action='stop' to end recording.",
                    verification={"action": "start", "state": "listening"}
                )
            return ToolResult(success=False, error="Already listening or voice not ready")

        elif action == "stop":
            text = voice_pipeline.push_to_talk_stop()
            if text is not None:
                return ToolResult(
                    success=True,
                    data=text,
                    verification={"action": "stop", "transcribed": True, "text_length": len(text)}
                )
            return ToolResult(success=False, error="Not recording or no speech detected")

        return ToolResult(success=False, error="Invalid action. Use 'start' or 'stop'")


class VoiceSpeakTool(BaseTool):
    """Speak text using TTS"""
    name = "voice_speak"
    description = "Speak text using text-to-speech"
    category = ToolCategory.VOICE
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="text", type="string", description="Text to speak"),
    ]

    def execute(self, text: str) -> ToolResult:
        if not text or not text.strip():
            return ToolResult(success=False, error="Empty text")

        success = voice_pipeline.speak(text)
        if success:
            return ToolResult(
                success=True,
                data=f"Spoke: {text[:50]}{'...' if len(text) > 50 else ''}",
                verification={"action": "speak", "text_length": len(text)}
            )
        return ToolResult(success=False, error="TTS failed")


class VoiceStatusTool(BaseTool):
    """Get voice pipeline status"""
    name = "voice_status"
    description = "Get current voice pipeline status"
    category = ToolCategory.VOICE
    permission_level = PermissionLevel.READ
    parameters = []

    def execute(self) -> ToolResult:
        return ToolResult(
            success=True,
            data={
                "state": voice_pipeline.state.value,
                "ready": voice_pipeline.is_ready(),
                "listening": voice_pipeline.is_listening,
                "speaking": voice_pipeline.is_speaking,
            },
            verification={"action": "status"}
        )


# Register voice tools
tool_registry.register(VoiceListenTool())
tool_registry.register(VoiceSpeakTool())
tool_registry.register(VoiceStatusTool())