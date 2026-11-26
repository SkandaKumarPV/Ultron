"""ULTRON Voice Pipeline - Push-to-talk, STT, TTS"""
import threading
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity
from ultron.voice.stt import MISSING_KEY, TranscriptionError, has_api_key, transcribe_pcm


class VoiceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class VoiceConfig:
    push_to_talk: bool = True
    wake_word: bool = False
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024
    tts_engine: str = "piper"
    tts_voice: str = "en_US-lessac-medium"
    tts_voice_path: str = ""
    silence_threshold: int = 500
    silence_duration: float = 1.5
    max_record_duration: float = 30.0


class VoicePipeline:
    """Voice input/output pipeline with push-to-talk"""

    def __init__(self):
        self._config = VoiceConfig()
        self._state = VoiceState.IDLE
        self._tts_voice = None
        self._running = False
        self._callback: Optional[Callable[[str], None]] = None
        self._lock = threading.RLock()
        
        # Audio recording
        self._audio_thread: Optional[threading.Thread] = None
        self._audio_buffer: list = []
        self._recording = False
        self._pyaudio = None
        self._stream = None

    def initialize(self):
        """Initialize voice components"""
        cfg = config.config.voice
        
        self._config.push_to_talk = cfg.push_to_talk
        self._config.wake_word = cfg.wake_word
        self._config.sample_rate = cfg.sample_rate
        self._config.channels = cfg.channels
        self._config.tts_engine = cfg.tts_engine
        self._config.tts_voice = cfg.tts_voice
        
        # Set voice path
        voice_path = Path(config.resolve_path(f"voices/{cfg.tts_voice}.onnx"))
        if voice_path.exists():
            self._config.tts_voice_path = str(voice_path)
        
        # Load TTS (Piper)
        self._load_tts()
        
        # Initialize PyAudio for recording
        self._init_audio()

    def _load_tts(self):
        """Load Piper TTS voice"""
        try:
            from piper import PiperVoice
            
            if self._config.tts_voice_path and Path(self._config.tts_voice_path).exists():
                self._tts_voice = PiperVoice.load(self._config.tts_voice_path)
                audit_log(AuditEventType.MODEL_LOADED, severity=AuditSeverity.INFO,
                          details={"model": self._config.tts_voice, "type": "tts", "engine": "piper"})
            else:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                          details={"message": f"TTS voice not found: {self._config.tts_voice_path}"})
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Failed to load TTS voice: {e}"})

    def _init_audio(self):
        """Initialize PyAudio for recording"""
        try:
            import pyaudio
            if self._pyaudio is None:
                self._pyaudio = pyaudio.PyAudio()
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                      details={"message": f"PyAudio not available: {e}"})
            self._pyaudio = None

    def start(self, callback: Optional[Callable[[str], None]] = None):
        """Start voice pipeline"""
        with self._lock:
            self._callback = callback
            if self._running:
                return
            self._running = True
            self._state = VoiceState.IDLE

    def stop(self):
        """Stop voice pipeline"""
        with self._lock:
            self._running = False
            self._state = VoiceState.IDLE
            self._stop_recording()

    def _start_recording(self):
        """Start audio recording"""
        if not self._pyaudio:
            return False
            
        if self._recording:
            return True

        self._recording = True
        self._audio_buffer = []
        
        try:
            import pyaudio
            self._stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=self._config.channels,
                rate=self._config.sample_rate,
                input=True,
                frames_per_buffer=self._config.chunk_size,
                stream_callback=self._audio_callback
            )
            self._stream.start_stream()
            audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                      details={"action": "recording_started", "sample_rate": self._config.sample_rate,
                               "channels": self._config.channels})
            return True
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Failed to start recording: {e}"})
            self._recording = False
            return False

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback for recording"""
        import pyaudio
        if self._recording:
            self._audio_buffer.append(in_data)
        return (in_data, pyaudio.paContinue)

    def _stop_recording(self) -> Optional[bytes]:
        """Stop recording and return audio data"""
        if not self._recording:
            return None
            
        self._recording = False
        
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        # Combine audio chunks
        if self._audio_buffer:
            audio_data = b''.join(self._audio_buffer)
            self._audio_buffer = []
            audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                      details={"action": "recording_stopped", "audio_bytes": len(audio_data)})
            return audio_data
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.WARNING,
                  details={"action": "recording_stopped", "audio_bytes": 0})
        return None

    def push_to_talk_start(self) -> bool:
        """Start push-to-talk recording"""
        with self._lock:
            if not self._running or self._state != VoiceState.IDLE or not self.is_ready():
                return False
            self._state = VoiceState.LISTENING
            audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                      details={"action": "push_to_talk_start"})
            if self._start_recording():
                return True
            self._state = VoiceState.IDLE
            return False

    def push_to_talk_stop(self) -> Optional[str]:
        """Stop push-to-talk and return transcribed text"""
        with self._lock:
            if not self._running or self._state != VoiceState.LISTENING:
                return None
            self._state = VoiceState.PROCESSING

        # Get recorded audio
        audio_data = self._stop_recording()
        
        if not audio_data:
            with self._lock:
                self._state = VoiceState.IDLE
            return None


        try:
            text = self._transcribe_audio(audio_data)
        finally:
            with self._lock:
                self._state = VoiceState.IDLE

        if text and self._callback:
            try:
                self._callback(text)
            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                          details={"message": f"Voice callback error: {e}"})

        return text

    def _transcribe_audio(self, audio_data: bytes) -> Optional[str]:
        try:
            text = transcribe_pcm(audio_data, self._config.sample_rate, self._config.channels)
        except TranscriptionError as error:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                      details={"component": "speech_to_text", "message": str(error)})
            raise
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "transcribe", "text_length": len(text or "")})
        return text

    def speak(self, text: str) -> bool:
        """Speak text using Piper TTS"""
        if not self._tts_voice:
            # Fallback to Windows SAPI
            return self._speak_sapi(text)

        with self._lock:
            if not self._running:
                return False
            self._state = VoiceState.SPEAKING

        try:
            # Generate audio with Piper (new API)
            chunks = list(self._tts_voice.synthesize(text))
            
            audio_data = b''.join(chunk.audio_int16_bytes for chunk in chunks)
            
            # Play audio
            self._play_audio(audio_data)
            
            audit_log(AuditEventType.VOICE_OUTPUT, severity=AuditSeverity.INFO,
                      details={"action": "speak", "text_length": len(text)})
            return True
            
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"TTS error: {e}"})
            # Fallback
            return self._speak_sapi(text)
        finally:
            with self._lock:
                self._state = VoiceState.IDLE

    def _speak_sapi(self, text: str) -> bool:
        """Fallback TTS using Windows SAPI"""
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak(text)
            return True
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"SAPI TTS failed: {e}"})
            return False

    def _play_audio(self, audio_data: bytes):
        """Play raw audio data"""
        if not self._pyaudio:
            return
            
        try:
            stream = self._pyaudio.open(
                format=self._pyaudio.get_format_from_width(2),
                channels=1,
                rate=22050,  # Piper default sample rate
                output=True
            )
            stream.write(audio_data)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"message": f"Audio playback error: {e}"})

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def is_listening(self) -> bool:
        return self._state == VoiceState.LISTENING

    @property
    def is_speaking(self) -> bool:
        return self._state == VoiceState.SPEAKING

    def is_ready(self) -> bool:
        """Check if voice components are ready"""
        return self.input_error() is None

    def input_error(self) -> Optional[str]:
        if not has_api_key():
            return MISSING_KEY
        if self._pyaudio is None:
            return "Microphone input is unavailable. Check that PyAudio is installed."
        return None


# Global instance
voice_pipeline = VoicePipeline()
