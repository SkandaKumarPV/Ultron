"""ULTRON Image Tools - Image generation via tool registry"""
from typing import Optional

from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.security.permissions import PermissionLevel
from ultron.image import image_generator, ImageBackend, ImageStyle
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

STYLES = [s.value for s in ImageStyle]
BACKENDS = [b.value for b in ImageBackend]
IMG_CATEGORY = ToolCategory.SYSTEM


class GenerateImageTool(BaseTool):
    """Generate an image from a text prompt"""
    name = "generate_image"
    description = (
        "Generate an image from a text description. Uses Stable Diffusion when "
        "available, otherwise a fast procedural fallback. Never asks for internet."
    )
    category = IMG_CATEGORY
    permission_level = PermissionLevel.EXECUTE
    parameters = [
        ToolParameter(name="prompt", type="string",
                      description="What to draw, e.g. 'a sunset over mountains'"),
        ToolParameter(name="style", type="string",
                      description="Visual style", enum=STYLES, required=False, default="none"),
        ToolParameter(name="width", type="integer", description="Width in px",
                      required=False, default=512),
        ToolParameter(name="height", type="integer", description="Height in px",
                      required=False, default=512),
        ToolParameter(name="backend", type="string",
                      description="auto | procedural | diffusion",
                      enum=BACKENDS, required=False, default="auto"),
        ToolParameter(name="seed", type="integer",
                      description="Random seed for reproducible results", required=False),
    ]

    def execute(self, prompt: str, style: str = "none", width: int = 512,
                height: int = 512, backend: str = "auto",
                seed: Optional[int] = None) -> ToolResult:
        width = max(64, min(int(width), 1024))
        height = max(64, min(int(height), 1024))
        result = image_generator.generate(
            prompt=prompt, width=width, height=height, style=style,
            backend=backend, seed=seed,
        )
        if result.success:
            return ToolResult(
                success=True,
                data={
                    "path": result.path,
                    "backend": result.backend,
                    "seed": result.seed,
                    "size": [result.width, result.height],
                    "generation_time_ms": round(result.generation_time_ms),
                },
                verification={"action": "generate_image", "backend": result.backend}
            )
        return ToolResult(success=False,
                         error=result.error or "Image generation failed")


class ImageStatusTool(BaseTool):
    """Get image generator status"""
    name = "image_status"
    description = "Check whether image generation is ready and which backend is active"
    category = IMG_CATEGORY
    permission_level = PermissionLevel.READ
    parameters = []

    def execute(self) -> ToolResult:
        status = image_generator.get_status()
        status["history"] = image_generator.get_history(limit=10)
        return ToolResult(success=True, data=status,
                          verification={"action": "image_status"})


class ImageHistoryTool(BaseTool):
    """List recently generated images"""
    name = "image_history"
    description = "List recently generated images with their prompts"
    category = IMG_CATEGORY
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="limit", type="integer",
                      description="Max entries to return", required=False, default=20),
    ]

    def execute(self, limit: int = 20) -> ToolResult:
        history = image_generator.get_history(limit=limit)
        return ToolResult(
            success=True,
            data={"count": len(history), "images": history},
            verification={"action": "image_history"}
        )


# Register image tools
tool_registry.register(GenerateImageTool())
tool_registry.register(ImageStatusTool())
tool_registry.register(ImageHistoryTool())
