"""ULTRON Embedder - Local embeddings using sentence-transformers"""
from typing import List, Optional, Union
from pathlib import Path
import numpy as np
import threading

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class Embedder:
    """Local text embeddings using sentence-transformers (CPU by default)"""

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
        self._model_name = None
        self._device = "auto"
        self._embedding_dim = 384  # all-MiniLM-L6-v2
        self._initialized = True

    def initialize(self, model_name: str = None, device: str = None):
        """Load the embedding model"""
        if self._model is not None:
            return True

        cfg = config.config.workspace
        model_name = model_name or cfg.embedder_model
        device = device or cfg.embedder_device

        try:
            from sentence_transformers import SentenceTransformer

            if device == "auto":
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            try:
                self._model = SentenceTransformer(model_name, device=device)
            except Exception:
                if device != "cuda":
                    raise
                device = "cpu"
                self._model = SentenceTransformer(model_name, device=device)
            self._model_name = model_name
            self._device = device
            self._embedding_dim = self._model.get_sentence_embedding_dimension()

            audit_log(AuditEventType.MODEL_LOADED, severity=AuditSeverity.INFO,
                      details={"model": model_name, "type": "embedder",
                               "dim": self._embedding_dim, "device": device})
            return True

        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Failed to load embedder: {e}"})
            return False

    def embed(self, texts: Union[str, List[str]]) -> np.ndarray:
        """Generate embeddings for text(s)"""
        if self._model is None:
            if not self.initialize():
                raise RuntimeError("Embedder not initialized")

        if isinstance(texts, str):
            texts = [texts]

        embeddings = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return embeddings

    def embed_single(self, text: str) -> np.ndarray:
        """Generate embedding for single text"""
        return self.embed([text])[0]

    def get_dimension(self) -> int:
        return self._embedding_dim

    def is_loaded(self) -> bool:
        return self._model is not None


# Global instance
embedder = Embedder()