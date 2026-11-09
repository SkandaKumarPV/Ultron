"""ULTRON Model Loader and Manager"""
import os
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, AsyncGenerator
from dataclasses import dataclass, field

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


@dataclass
class ModelInfo:
    name: str
    path: str
    n_ctx: int
    n_gpu_layers: int
    loaded: bool = False
    load_time: float = 0
    vram_used_mb: float = 0
    ram_used_mb: float = 0
    last_used: float = 0


class ModelManager:
    """Manages local LLM loading, inference, and resource monitoring"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._model = None
        self._model_info: Optional[ModelInfo] = None
        self._lock = threading.RLock()
        self._idle_timer: Optional[threading.Timer] = None
        self._initialized = True

    def initialize(self):
        """Initialize model manager (load config, verify paths)"""
        cfg = config.config
        model_path = config.get_model_path()

        if not model_path.exists():
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                      details={"message": f"Model file not found: {model_path}"})
            return False

        self._model_info = ModelInfo(
            name=cfg.model.name,
            path=str(model_path),
            n_ctx=cfg.model.n_ctx,
            n_gpu_layers=cfg.model.n_gpu_layers,
        )
        return True

    def load(self) -> bool:
        """Load the model into memory"""
        with self._lock:
            if self._model is not None:
                return True

            if not self._model_info:
                if not self.initialize():
                    return False

            cfg = config.config
            model_path = self._model_info.path

            try:
                # Import llama-cpp-python
                from llama_cpp import Llama, llama_supports_gpu_offload

                # Load model
                start_time = time.time()
                requested_gpu_layers = cfg.model.n_gpu_layers
                gpu_layers = requested_gpu_layers
                if requested_gpu_layers != 0 and not llama_supports_gpu_offload():
                    audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                              details={"message": "llama.cpp has no CUDA support; using CPU layers"})
                    gpu_layers = 0
                self._model = Llama(
                    model_path=model_path,
                    n_ctx=cfg.model.n_ctx,
                    n_gpu_layers=gpu_layers,
                    n_batch=cfg.model.n_batch,
                    n_threads=cfg.model.n_threads,
                    verbose=cfg.model.verbose,
                    use_mmap=True,
                    use_mlock=False,
                    offload_kqv=True,
                )
                load_time = time.time() - start_time

                # Estimate VRAM usage (rough)
                # For Q4_K_M: ~1.1 GB weights + KV cache
                self._model_info.loaded = True
                self._model_info.n_gpu_layers = gpu_layers
                self._model_info.load_time = load_time
                self._model_info.vram_used_mb = 1100  # Approximate for 1.5B Q4_K_M
                self._model_info.ram_used_mb = 500
                self._model_info.last_used = time.time()

                # Start idle timer
                self._reset_idle_timer()

                audit_log(AuditEventType.MODEL_LOADED, severity=AuditSeverity.INFO,
                          details={"model": cfg.model.name, "load_time_sec": load_time,
                                   "vram_mb": self._model_info.vram_used_mb})

                return True

            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                          details={"message": f"Failed to load model: {e}"})
                self._model = None
                return False

    def unload(self):
        """Unload model from memory"""
        with self._lock:
            if self._model is None:
                return

            self._cancel_idle_timer()
            self._model = None
            if self._model_info:
                self._model_info.loaded = False

            audit_log(AuditEventType.MODEL_UNLOADED, severity=AuditSeverity.INFO,
                      details={"model": self._model_info.name if self._model_info else "unknown"})

    def is_loaded(self) -> bool:
        return self._model is not None

    def get_model_info(self) -> Dict[str, Any]:
        if self._model_info:
            return {
                "name": self._model_info.name,
                "path": self._model_info.path,
                "loaded": self._model_info.loaded,
                "n_ctx": self._model_info.n_ctx,
                "n_gpu_layers": self._model_info.n_gpu_layers,
                "load_time_sec": self._model_info.load_time,
                "vram_used_mb": self._model_info.vram_used_mb,
                "ram_used_mb": self._model_info.ram_used_mb,
                "last_used": self._model_info.last_used,
            }
        return {"loaded": False}

    def _reset_idle_timer(self):
        """Reset idle unload timer"""
        self._cancel_idle_timer()
        cfg = config.config
        timeout = cfg.resources.unload_idle_after_seconds
        self._idle_timer = threading.Timer(timeout, self._idle_unload)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _cancel_idle_timer(self):
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _idle_unload(self):
        """Called when model has been idle too long"""
        with self._lock:
            if self._model_info and (time.time() - self._model_info.last_used) >= config.config.resources.unload_idle_after_seconds:
                self.unload()

    def _update_last_used(self):
        if self._model_info:
            self._model_info.last_used = time.time()
        self._reset_idle_timer()

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7, **kwargs) -> str:
        """Synchronous generation"""
        with self._lock:
            if not self._model:
                if not self.load():
                    return "Error: Model not loaded"

            self._update_last_used()

            try:
                output = self._model(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=kwargs.get("top_p", 0.95),
                    top_k=kwargs.get("top_k", 40),
                    repeat_penalty=kwargs.get("repeat_penalty", 1.1),
                    stop=kwargs.get("stop", ["</s>", "<|endoftext|>"]),
                    echo=False,
                )
                return output["choices"][0]["text"].strip()
            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                          details={"message": f"Generation error: {e}"})
                return f"Error: {e}"

    async def generate_async(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7, **kwargs) -> str:
        """Asynchronous generation"""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.generate(prompt, max_tokens, temperature, **kwargs))

    def generate_stream(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7, **kwargs):
        """Streaming generation"""
        with self._lock:
            if not self._model:
                if not self.load():
                    yield "Error: Model not loaded"
                    return

            self._update_last_used()

            try:
                stream = self._model(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=kwargs.get("top_p", 0.95),
                    top_k=kwargs.get("top_k", 40),
                    repeat_penalty=kwargs.get("repeat_penalty", 1.1),
                    stop=kwargs.get("stop", ["</s>", "<|endoftext|>"]),
                    stream=True,
                )
                for chunk in stream:
                    if chunk["choices"]:
                        yield chunk["choices"][0]["text"]
            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                          details={"message": f"Stream generation error: {e}"})
                yield f"Error: {e}"

    def chat_completion(self, messages: List[Dict], **kwargs) -> str:
        """Chat completion format"""
        with self._lock:
            if not self._model:
                if not self.load():
                    return "Error: Model not loaded"

            self._update_last_used()

            try:
                output = self._model.create_chat_completion(
                    messages=messages,
                    max_tokens=kwargs.get("max_tokens", 512),
                    temperature=kwargs.get("temperature", 0.7),
                    top_p=kwargs.get("top_p", 0.95),
                    top_k=kwargs.get("top_k", 40),
                    repeat_penalty=kwargs.get("repeat_penalty", 1.1),
                    stop=kwargs.get("stop", ["</s>", "<|endoftext|>"]),
                )
                return output["choices"][0]["message"]["content"].strip()
            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                          details={"message": f"Chat completion error: {e}"})
                return f"Error: {e}"

    def get_resource_usage(self) -> Dict[str, float]:
        """Get current resource usage"""
        import psutil
        process = psutil.Process()
        mem = process.memory_info()

        return {
            "ram_mb": mem.rss / (1024 * 1024),
            "vram_mb": self._model_info.vram_used_mb if self._model_info else 0,
            "cpu_percent": process.cpu_percent(),
        }


# Global instance
model_manager = ModelManager()