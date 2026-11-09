"""ULTRON Image Generation

Two backends:
  1. "diffusion" - Stable Diffusion via diffusers (real AI image gen)
  2. "procedural" - fast PIL-based generative art (always works, no model download)

The diffusion backend is optional; it can be blocked on Windows (missing
drivers, antivirus quarantining ONNX/DLLs, low VRAM). When it is unavailable,
ULTRON falls back to procedural and reports why, so the user can grant
permission in the Control Center GUI instead of silently failing.
"""
import os
import json
import math
import random
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class ImageBackend(Enum):
    AUTO = "auto"            # try diffusion, fall back to procedural
    PROCEDURAL = "procedural"
    DIFFUSION = "diffusion"


class ImageStyle(Enum):
    NONE = "none"
    PHOTOREALISTIC = "photorealistic"
    ANIME = "anime"
    DIGITAL_ART = "digital art"
    OIL_PAINTING = "oil painting"
    WATERCOLOR = "watercolor"
    CYBERPUNK = "cyberpunk"
    SKETCH = "pencil sketch"


@dataclass
class ImageResult:
    success: bool
    path: Optional[str] = None
    backend: str = "procedural"
    prompt: str = ""
    width: int = 512
    height: int = 512
    seed: int = 0
    generation_time_ms: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _resolve_output_dir() -> Path:
    out = Path(config.resolve_path("generated_images"))
    out.mkdir(parents=True, exist_ok=True)
    return out


class ImageGenerator:
    """Unified image generation facade."""

    # Lightweight, CPU-friendly diffusion models
    MODEL_CHOICES = [
        "segmind/tiny-sd",              # ~400MB - fastest, CPU viable
        "stable-diffusion-v1-5/stable-diffusion-v1-5",  # ~5GB fp32 / 2GB fp16
        "stabilityai/sd-turbo",          # ~7GB fp32 / 3.5GB fp16 - 4 steps
    ]
    DEFAULT_MODEL = "segmind/tiny-sd"

    def __init__(self):
        self._backend = ImageBackend.AUTO
        self._model = None
        self._model_id: Optional[str] = None
        self._pipe = None
        self._device = "cpu"
        self._output_dir: Optional[Path] = None
        self._history: List[ImageResult] = []
        self._diffusion_error: Optional[str] = None
        self._lock = None  # created lazily (thread-safe)
        import threading
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def initialize(self):
        self._output_dir = _resolve_output_dir()
        audit_log(AuditEventType.APP_LAUNCHED, severity=AuditSeverity.INFO,
                  details={"component": "image_generator", "output_dir": str(self._output_dir)})

    @property
    def output_dir(self) -> Path:
        if self._output_dir is None:
            self._output_dir = _resolve_output_dir()
        return self._output_dir

    def set_backend(self, backend: str):
        try:
            self._backend = ImageBackend(backend)
        except ValueError:
            self._backend = ImageBackend.AUTO
        return self._backend.value

    def get_backend(self) -> str:
        return self._backend.value

    def get_status(self) -> Dict[str, Any]:
        ready = self._pipe is not None
        return {
            "requested_backend": self._backend.value,
            "diffusion_loaded": ready,
            "diffusion_model": self._model_id,
            "device": self._device,
            "diffusion_error": self._diffusion_error,
            "procedural_available": True,
            "output_dir": str(self.output_dir),
            "images_generated": len(self._history),
        }

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def generate(self, prompt: str, width: int = 512, height: int = 512,
                 style: str = "none", backend: Optional[str] = None,
                 steps: Optional[int] = None, guidance: float = 7.5,
                 seed: Optional[int] = None) -> ImageResult:
        """Generate an image from a text prompt. Never raises."""
        started = time.time()
        prompt = (prompt or "").strip() or "abstract art"
        seed = seed if seed is not None else random.randint(0, 2**31 - 1)

        requested = backend or self._backend.value
        used_backend = "procedural"
        path = None
        error = None

        if requested in ("diffusion", "auto"):
            try:
                if not self._ensure_diffusion(steps):
                    raise RuntimeError(self._diffusion_error or "diffusion unavailable")
                path = self._run_diffusion(prompt, width, height, style, steps, guidance, seed)
                used_backend = "diffusion"
            except Exception as e:
                self._diffusion_error = f"{type(e).__name__}: {e}"
                error = f"Diffusion unavailable ({self._diffusion_error}); used procedural fallback."
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                          details={"message": "diffusion failed", "error": self._diffusion_error})

        if path is None and requested != "diffusion":
            try:
                path = self._run_procedural(prompt, width, height, style, seed)
                used_backend = "procedural"
            except Exception as e:
                error = f"Image fallback failed ({type(e).__name__}: {e})"
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                          details={"component": "image_generator", "error": error})
        elif path is None and requested == "diffusion":
            # user forced diffusion and it failed -> hard fail
            result = ImageResult(success=False, backend="diffusion", prompt=prompt,
                                 width=width, height=height, seed=seed, error=error)
            self._history.append(result)
            return result

        if path is None:
            result = ImageResult(success=False, backend=used_backend, prompt=prompt,
                                 width=width, height=height, seed=seed, error=error)
            self._history.append(result)
            return result

        elapsed = (time.time() - started) * 1000
        result = ImageResult(
            success=True, path=str(path), backend=used_backend, prompt=prompt,
            width=width, height=height, seed=seed, generation_time_ms=elapsed,
            error=error,
            metadata={"style": style, "requested_backend": requested,
                      "diffusion_error": self._diffusion_error if used_backend != "diffusion" else None},
        )
        self._history.append(result)
        audit_log(AuditEventType.FILE_WRITE, severity=AuditSeverity.INFO,
                  details={"component": "image_generator", "path": str(path),
                           "backend": used_backend, "prompt_len": len(prompt)})
        return result

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {"path": r.path, "prompt": r.prompt, "backend": r.backend, "seed": r.seed,
                 "created": r.generation_time_ms}
                for r in self._history[-limit:] if r.success
            ]

    def open_output_folder(self) -> str:
        try:
            os.startfile(str(self.output_dir))  # noqa: S606 - user-requested action
        except Exception as e:
            return f"Could not open folder: {e}"
        return str(self.output_dir)

    # ------------------------------------------------------------------
    # diffusion backend
    # ------------------------------------------------------------------
    def _ensure_diffusion(self, steps: Optional[int]) -> bool:
        if self._pipe is not None:
            return True
        if self._diffusion_error and self._pipe is None and self._backend == ImageBackend.AUTO:
            # retry anyway - conditions may have changed (permissions granted)
            self._diffusion_error = None
        try:
            import torch
            from diffusers import DiffusionPipeline  # noqa: F401
        except Exception as e:
            self._diffusion_error = f"dependencies missing ({type(e).__name__}: {e})"
            return False

        # Loading even tiny diffusion checkpoints can exhaust a Windows
        # process's address space on CPU.  In auto mode, use the guaranteed
        # procedural backend unless CUDA is available; an explicit diffusion
        # request still reports a normal failure instead of silently changing
        # the requested backend.
        if self._backend == ImageBackend.AUTO and not torch.cuda.is_available():
            self._diffusion_error = "auto diffusion disabled on CPU to prevent out-of-memory crashes"
            return False

        model_id = self._model_id or self.DEFAULT_MODEL
        try:
            import torch
            # CPU safetensors/fp16 checkpoints cause "Half vs Float" matmul errors,
            # so force float32 unless we have CUDA.
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            from diffusers import StableDiffusionPipeline, DiffusionPipeline
            load_path = self._local_model_path(model_id)
            if load_path:
                pipe = StableDiffusionPipeline.from_single_file(load_path, torch_dtype=dtype)
            else:
                pipe = DiffusionPipeline.from_pretrained(model_id, torch_dtype=dtype)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            pipe = pipe.to(self._device)

            # Keep the diffusion pipeline on CUDA when available. CPU offload
            # is reserved for CPU mode because it increases CPU traffic.
            try:
                pipe.enable_attention_slicing()
                if self._device == "cpu":
                    pipe.enable_model_cpu_offload()
            except Exception:
                pass

            self._pipe = pipe
            self._model_id = model_id
            audit_log(AuditEventType.MODEL_LOADED, severity=AuditSeverity.INFO,
                      details={"model": model_id, "type": "image-diffusion"})
            return True
        except Exception as e:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            self._diffusion_error = f"{type(e).__name__}: {e}"
            return False

    def _local_model_path(self, model_id: str) -> Optional[Path]:
        """Allow locally staged .safetensors checkpoints (bypasses HF downloads)."""
        models_dir = Path(config.resolve_path("models"))
        for cand in [
            models_dir / f"{model_id.split('/')[-1]}.safetensors",
            models_dir / f"{model_id.split('/')[-1]}.ckpt",
        ]:
            if cand.exists():
                return cand
        return None

    def _run_diffusion(self, prompt, width, height, style, steps, guidance, seed) -> str:
        import torch
        negative = ""
        if style and style != "none":
            prompt = f"{prompt}, {style}"
        default_steps = 4 if (self._model_id and "turbo" in self._model_id) else 20
        n_steps = steps or default_steps
        generator = torch.Generator(device=getattr(self, "_device", "cpu")).manual_seed(int(seed))
        with torch.inference_mode():
            out = self._pipe(
                prompt, negative_prompt=negative, height=height, width=width,
                num_inference_steps=n_steps, guidance_scale=guidance,
                generator=generator,
            )
        image = out.images[0]
        return self._save_pil(image, prompt, "diffusion")

    # ------------------------------------------------------------------
    # procedural backend (always available)
    # ------------------------------------------------------------------
    def _run_procedural(self, prompt, width, height, style, seed) -> str:
        try:
            from PIL import Image, ImageDraw, ImageFilter
        except Exception as e:
            raise RuntimeError(f"PIL unavailable: {e}")

        from PIL import Image, ImageDraw, ImageFilter, ImageFont

        rng = random.Random(seed)
        img = Image.new("RGB", (width, height), self._style_bg(style, rng))
        d = ImageDraw.Draw(img, "RGBA")

        palette = self._palette(prompt, style, rng)

        # layered organic shapes driven by the prompt hash
        h = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), 16)
        n_shapes = 5 + (h % 12)
        for i in range(n_shapes):
            s = rng.choice(["circle", "triangle", "line", "curve"])
            col = palette[i % len(palette)]
            alpha = rng.randint(60, 190)
            if s == "circle":
                r = rng.randint(width // 14, width // 3)
                x, y = rng.randint(0, width), rng.randint(0, height)
                d.ellipse([x - r, y - r, x + r, y + r], fill=col + (alpha,))
            elif s == "triangle":
                x, y = rng.randint(0, width), rng.randint(0, height)
                s2 = rng.randint(width // 12, width // 4)
                d.polygon([(x, y), (x - s2, y + s2), (x + s2, y + s2)], fill=col + (alpha,))
            elif s == "line":
                d.line([(rng.randint(0, width), rng.randint(0, height)),
                        (rng.randint(0, width), rng.randint(0, height))],
                       fill=col + (alpha,), width=rng.randint(2, max(3, width // 90)))
            else:
                pts = [(rng.randint(0, width), rng.randint(0, height)) for _ in range(4)]
                d.line(pts, fill=col + (alpha,), width=rng.randint(3, max(4, width // 60)))

        # neural-net style connecting nodes (matches the ULTRON identity)
        nodes = []
        for _ in range(rng.randint(8, 18)):
            nodes.append((rng.randint(10, width - 10), rng.randint(10, height - 10)))
        for i, (x1, y1) in enumerate(nodes):
            for (x2, y2) in nodes[i + 1:]:
                if math.hypot(x2 - x1, y2 - y1) < width / 4:
                    d.line([(x1, y1), (x2, y2)], fill=(137, 180, 250, 70), width=1)
            r = rng.randint(3, 7)
            d.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=(205, 214, 244, 210))

        # depth blur
        img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.3, 1.2)))
        img = self._grade(img, style)

        # prompt watermark
        self._stamp(img, prompt, seed)
        return self._save_pil(img, prompt, "procedural")

    @staticmethod
    def _style_bg(style, rng) -> tuple:
        table = {
            "cyberpunk": (10, 8, 26),
            "photorealistic": (18, 18, 22),
            "anime": (28, 22, 40),
            "oil painting": (34, 26, 18),
            "watercolor": (245, 243, 235),
            "sketch": (246, 244, 240),
            "digital art": (16, 16, 24),
        }
        col = table.get(style)
        if col:
            jitter = tuple(max(0, min(255, c + rng.randint(-14, 14))) for c in col)
            return jitter
        return (rng.randint(14, 30), rng.randint(14, 30), rng.randint(24, 40))

    @staticmethod
    def _palette(prompt: str, style: str, rng) -> List[tuple]:
        curated = {
            "cyberpunk": [(255, 0, 128), (0, 255, 240), (140, 0, 255), (255, 200, 0)],
            "anime": [(255, 130, 170), (130, 200, 255), (255, 220, 140), (180, 140, 255)],
            "oil painting": [(200, 140, 80), (120, 160, 90), (200, 90, 70), (80, 110, 160)],
            "watercolor": [(150, 200, 230), (240, 180, 200), (200, 230, 180), (240, 220, 160)],
            "sketch": [(60, 60, 60), (120, 120, 120), (40, 40, 40)],
            "photorealistic": [(90, 120, 160), (150, 130, 100), (110, 150, 110), (170, 160, 150)],
            "digital art": [(137, 180, 250), (203, 166, 247), (245, 194, 231), (166, 227, 161)],
        }
        if style in curated:
            return curated[style]
        base = int(hashlib.sha256(prompt.encode()).hexdigest()[:6], 16)
        return [((base >> (i * 8)) & 0xFF, (base >> (i * 4)) & 0xFF, (base >> i) & 0xFF) for i in range(4)]

    def _grade(self, img, style: str):
        from PIL import ImageEnhance
        table = {"photorealistic": (1.0, 1.15, 0.95), "anime": (1.15, 1.3, 1.0),
                 "cyberpunk": (1.3, 1.5, 0.9), "sketch": (0.7, 0.6, 1.1),
                 "watercolor": (0.85, 0.9, 1.15), "oil painting": (1.05, 1.1, 0.95)}
        c, s, b = table.get(style, (1.0, 1.0, 1.0))
        if c != 1.0:
            img = ImageEnhance.Color(img).enhance(c)
        if s != 1.0:
            img = ImageEnhance.Sharpness(img).enhance(s)
        if b != 1.0:
            img = ImageEnhance.Brightness(img).enhance(b)
        return img

    @staticmethod
    def _stamp(img, prompt: str, seed: int):
        try:
            from PIL import ImageDraw, ImageFont
            font_paths = [
                Path("C:/Windows/Fonts/segoeui.ttf"),
                Path("C:/Windows/Fonts/arial.ttf"),
            ]
            font = None
            for fp in font_paths:
                if fp.exists():
                    font = ImageFont.truetype(str(fp), max(12, img.width // 42))
                    break
            d = ImageDraw.Draw(img, "RGBA")
            ellipsis = "..." if len(prompt) > 46 else ""
            text = f"ULTRON | {prompt[:46]}{ellipsis} | seed {seed}"
            tw = d.textlength(text, font=font) if font else len(text) * 7
            pad = 8 if font else 6
            bar_h = (font.size if font else 14) + pad * 2
            d.rectangle([0, img.height - bar_h, img.width, img.height], fill=(0, 0, 0, 120))
            d.text((max(pad, (img.width - tw) / 2), img.height - bar_h + pad),
                   text, fill=(220, 225, 245, 230), font=font)
        except Exception:
            pass

    def _save_pil(self, img, prompt: str, backend: str) -> str:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        slug = "".join(c if c.isalnum() else "_" for c in prompt[:40]).strip("_") or "image"
        fname = f"ultron_{backend}_{stamp}_{slug}.png"
        path = self.output_dir / fname
        img.save(path, format="PNG")
        return str(path)


# Global instance
image_generator = ImageGenerator()
