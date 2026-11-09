"""ULTRON Wake Word Detection

Uses openWakeWord (ONNX runtime, CPU-friendly). Windows may block microphone
access (Settings > Privacy > Microphone). Instead of failing silently, this
module detects the block and returns a precise, actionable message that the GUI
shows in the Permissions tab so the user can grant access.

Wake word: "Hey ULTRON". openWakeWord ships pretrained acoustic models
(hey_jarvis, alexa, hey_mycroft) but not "ultron", so we:
  1. run the closest acoustic surrogate (hey_jarvis) via ONNX, AND
  2. verify the spoken keyword in the transcription (the word "ultron")

When a custom "ultron" ONNX model is trained/dropped into the models folder,
it is picked up automatically and used instead.
"""
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any
from enum import Enum

import numpy as np

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity

# Windows blocks: checkable states surfaced to the GUI
MIC_BLOCK_MESSAGE = (
    "Windows is blocking microphone access.\n\n"
    "To allow it: Settings > Privacy & security > Microphone > "
    "turn on 'Let desktop apps access the microphone', then restart ULTRON.\n\n"
    "You can also unlock it right now from the Voice tab."
)

# openWakeWord pretrained models used as the "ULTRON" acoustic trigger
DEFAULT_MODELS = ["hey_jarvis", "alexa", "hey_mycroft"]

# Words that must appear in the transcript for the command to be accepted
WAKE_WORDS = [
    "ultron", "ultralink", "al tron", "alt ron", "ultra on", "ultra one",
    "hailtron", "hail tron",
]


class WakeWordState(Enum):
    DORMANT = "dormant"      # hotkey not pressed
    ARMED = "armed"          # hotkey pressed, waiting for wake word
    LISTENING = "listening"  # wake word heard, capturing the command
    DETECTED = "detected"
    ERROR = "error"


def _models_dir() -> Path:
    """openWakeWord downloads pretrained models into its package resources dir."""
    try:
        import openwakeword
        d = Path(os.path.dirname(openwakeword.__file__)) / "resources" / "models"
        return d
    except Exception:
        return Path()


def resolve_wake_models() -> List[str]:
    """Prefer locally trained 'ultron' model, else ONNX pretrained surrogates."""
    local = Path(config.resolve_path("models"))
    for cand in (local / "ultron.onnx", local / "hey_ultron.onnx"):
        if cand.exists():
            return [str(cand)]

    d = _models_dir()
    models = []
    for name in DEFAULT_MODELS:
        for ext in (".onnx", ".tflite"):
            p = d / f"{name}_v0.1{ext}"
            if p.exists():
                models.append(str(p))
                break
    return models


def contains_wake_word(text: str) -> Optional[str]:
    """Return the matched wake word variant, or None."""
    if not text:
        return None
    t = text.lower()
    t = re.sub(r"[^a-z ]", " ", t)
    for w in WAKE_WORDS:
        if re.search(rf"\b{w}\b", t):
            return w
    # common transcriptions "hey ultron", "ok ultron"
    if "ultron" in t:
        return "ultron"
    return None


class WakeWordDetector:
    """openWakeWord based detector + spoken-keyword verification."""

    def __init__(self):
        self._model = None
        self._state = WakeWordState.DORMANT
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._callback: Optional[Callable[[], None]] = None
        self._pyaudio = None
        self._stream = None
        self._sample_rate = 16000
        self._chunk = 1280
        self._error: Optional[str] = None
        self._threshold = 0.5
        self._available = False
        self._framework = None

    # ------------------------------------------------------------------
    def initialize(self) -> bool:
        """Load openWakeWord + mic. Returns True when ready to listen."""
        try:
            import pyaudio
            from openwakeword.model import Model

            models = resolve_wake_models()
            if not models:
                self._error = (
                    "No wake-word models found. Run: python -c "
                    "\"from openwakeword.utils import download_models; "
                    "download_models(model_names=['hey_jarvis'])\""
                )
                raise FileNotFoundError(self._error)

            self._pyaudio = pyaudio.PyAudio()

            # ONNX models exist for every pretrained wake word -> prefer them
            # (tflite runtime is not installed on Windows).
            framework = "onnx" if all(str(m).endswith(".onnx") for m in models) else "tflite"
            self._model = Model(wakeword_models=models, inference_framework=framework)
            self._framework = framework
            self._available = True
            self._state = WakeWordState.DORMANT
            audit_log(AuditEventType.MODEL_LOADED, severity=AuditSeverity.INFO,
                      details={"component": "wake_word", "models": models,
                               "framework": framework})
            return True
        except ImportError as e:
            self._error = f"openWakeWord not installed: {e}"
        except Exception as e:
            es = str(e).lower()
            if any(k in es for k in ("input device", "paerror", "line unavailable",
                                     "unanticipated host error")):
                self._error = MIC_BLOCK_MESSAGE
            else:
                self._error = f"{type(e).__name__}: {e}"
        audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                  details={"component": "wake_word", "error": self._error})
        self._state = WakeWordState.ERROR
        return False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def state(self) -> WakeWordState:
        return self._state

    def set_threshold(self, value: float):
        self._threshold = max(0.05, min(0.99, float(value)))

    # ------------------------------------------------------------------
    def start(self, callback: Callable[[], None]) -> bool:
        """Begin continuous listening in a background thread (armed state)."""
        if not self._available:
            if not self.initialize():
                return False
        if self._running:
            return True
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True,
                                        name="ultron-wakeword")
        self._thread.start()
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "wake_word_start"})
        return True

    def stop(self):
        self._running = False
        self._state = WakeWordState.DORMANT
        self._close_stream()
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "wake_word_stop"})

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------
    def _listen_loop(self):
        try:
            import pyaudio
            self._stream = self._pyaudio.open(
                format=pyaudio.paInt16, channels=1, rate=self._sample_rate,
                input=True, frames_per_buffer=self._chunk,
            )
        except Exception as e:
            es = str(e).lower()
            self._error = MIC_BLOCK_MESSAGE if any(
                k in es for k in ("input device", "line unavailable")) else str(e)
            self._state = WakeWordState.ERROR
            self._running = False
            return

        self._state = WakeWordState.ARMED
        cooldown_until = 0.0
        while self._running:
            try:
                data = self._stream.read(self._chunk, exception_on_overflow=False)
            except Exception:
                continue
            audio = np.frombuffer(data, dtype=np.int16)
            if time.time() < cooldown_until:
                continue
            try:
                preds = self._model.predict(audio)
            except Exception:
                continue
            for name, score in (preds or {}).items():
                if score >= self._threshold:
                    self._state = WakeWordState.DETECTED
                    audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                              details={"action": "wake_word_detected", "model": name,
                                       "score": round(float(score), 3)})
                    self._reset_model_buffer()
                    if self._callback:
                        try:
                            self._callback()
                        except Exception:
                            pass
                    cooldown_until = time.time() + 1.2
                    self._state = WakeWordState.ARMED
                    break
        self._close_stream()

    def _reset_model_buffer(self):
        try:
            # keep openWakeWord's internal prediction buffer from re-firing
            if hasattr(self._model, "prediction_buffer"):
                for k in list(self._model.prediction_buffer.keys()):
                    self._model.prediction_buffer[k].clear()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Diagnostic helper used by the GUI Permissions tab
    # ------------------------------------------------------------------
    def diagnose_microphone(self) -> Dict[str, Any]:
        """Probe the mic and report Windows permission state."""
        info: Dict[str, Any] = {"available": False, "devices": [], "blocked": False,
                                "message": "", "default_device": None}
        try:
            import pyaudio
            info["available"] = True
            pa = pyaudio.PyAudio()
            try:
                info["default_device"] = pa.get_default_input_device_info().get("name")
            except Exception:
                pass
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d.get("maxInputChannels", 0) > 0:
                    info["devices"].append(d.get("name", "unknown"))
            try:
                pa.open(format=pyaudio.paInt16, channels=1, rate=16000,
                        input=True, frames_per_buffer=512).close()
            except Exception:
                info["blocked"] = True
                info["message"] = MIC_BLOCK_MESSAGE
            pa.terminate()
        except ImportError:
            info["message"] = "PyAudio not installed"
        except Exception as e:
            info["blocked"] = True
            info["message"] = f"{type(e).__name__}: {e}"
        return info

    def open_windows_mic_settings(self) -> bool:
        """Open the Windows microphone privacy page so the user can grant access."""
        try:
            os.startfile("ms-settings:privacy-microphone")  # noqa: S606
            return True
        except Exception:
            return False


# Global instance
wake_word_detector = WakeWordDetector()
