"""ULTRON Voice Assistant - Alexa-style always-on voice control

Flow (like Alexa):
  1. Press Alt+` (or the mic button) -> assistant becomes ARMED
  2. Say "Hey ULTRON" + your command, e.g. "Hey ULTRON open notepad"
  3. ULTRON verifies its own name, runs the command through the kernel,
     and SPEAKS the answer back out loud.

Works fully locally: faster-whisper keyword wake detection + Piper (TTS) +
the ULTRON kernel (tools/PC control/workspace AI). The wake listener uses one
microphone stream so the wake phrase and its command stay in the same transcript.

Windows can block the microphone (Privacy settings). The assistant reports
that case precisely and offers the fix instead of hanging silently.
"""
import os
import re
import time
import queue
import threading
import tempfile
import wave
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

import numpy as np

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity
from ultron.voice.wake_word import (
    wake_word_detector, WakeWordState, contains_wake_word, MIC_BLOCK_MESSAGE,
)

WAKE_PHRASES = (
    "hey ultron", "ultron", "hey al tron", "hey altar on", "hey ultra on",
    "ultra on", "ultra one", "hailtron", "hail tron", "hello ultron",
)


class AssistantState(Enum):
    OFF = "off"              # engine off
    DORMANT = "dormant"      # running, waiting to be armed
    ARMED = "armed"          # hotkey pressed - listening for wake word
    CAPTURING = "capturing"  # wake word heard - recording the command
    THINKING = "thinking"    # running the command
    SPEAKING = "speaking"    # speaking the reply
    ERROR = "error"


def strip_wake_phrase(text: str) -> str:
    """Remove the wake phrase (and any leading greeting) so only the command remains."""
    if not text:
        return ""
    t = text.strip()
    low = t.lower()
    for p in sorted(WAKE_PHRASES, key=len, reverse=True):
        i = low.find(p)
        if i >= 0:
            rest = t[i + len(p):].strip(" ,.!?:;")
            return _strip_leading_greeting(rest)
    m = contains_wake_word(t)
    if m:
        i = low.find(m)
        return _strip_leading_greeting(t[i + len(m):].strip(" ,.!?:;"))
    return t


GREETINGS = ("hello", "hi", "hey", "ok", "okay", "please", "now", "can you", "could you")


def _strip_leading_greeting(text: str) -> str:
    low = text.lower()
    changed = True
    while changed:
        changed = False
        for g in GREETINGS:
            if low.startswith(g + " ") or low == g:
                text = text[len(g):].strip(" ,.!?:;")
                low = text.lower()
                changed = True
                break
    return text


def is_wake_phrase(text: str) -> bool:
    """Accept if the transcript clearly contains ULTRON's name."""
    return contains_wake_word(text) is not None


class VoiceAssistant:
    """Manages the complete local voice-interaction loop."""

    def __init__(self):
        self.state = AssistantState.OFF
        self.enabled = False
        self._lock = threading.RLock()
        self._wake_running = False
        self._pyaudio = None
        self._record_thread: Optional[threading.Thread] = None
        self._stt = None
        self._tts = None
        self._speak_threads: List[threading.Thread] = []
        self._manual_stop = threading.Event()
        self._manual_thread: Optional[threading.Thread] = None
        self._manual_text: Optional[str] = None
        self.last_transcript: Optional[str] = None
        self.last_response: Optional[str] = None
        self.error: Optional[str] = None
        self.stt_model_name = "base.en"
        self.reply_aloud = True
        # UI callbacks
        self.on_state_change: Optional[Callable[[AssistantState, Dict[str, Any]], None]] = None
        self.on_transcript: Optional[Callable[[str], None]] = None
        self.on_response: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def initialize(self) -> bool:
        """Load STT + TTS (wake word models load on demand)."""
        with self._lock:
            self._set_state(AssistantState.DORMANT)
        try:
            from ultron.voice.pipeline import voice_pipeline
            voice_pipeline.initialize()
            self._stt = voice_pipeline._stt_model
            self._tts = voice_pipeline._tts_voice
            self.stt_model_name = voice_pipeline._config.stt_model
            audit_log(AuditEventType.MODEL_LOADED, severity=AuditSeverity.INFO,
                      details={"component": "voice_assistant",
                               "stt": self.stt_model_name, "tts": "piper"})
            return True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self._set_state(AssistantState.ERROR, {"error": self.error})
            return False

    def start(self) -> bool:
        """Turn the assistant ON (dormant until armed)."""
        if self.enabled:
            return True
        if self._stt is None and not self.initialize():
            return False
        self.enabled = True
        self._set_state(AssistantState.DORMANT)
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "assistant_start"})
        return True

    def stop(self):
        """Turn the assistant OFF."""
        self.enabled = False
        self._wake_running = False
        wake_word_detector.stop()
        self._close_record_stream()
        self._set_state(AssistantState.OFF)
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "assistant_stop"})

    # ------------------------------------------------------------------
    # arming (hotkey / mic button)
    # ------------------------------------------------------------------
    def arm(self) -> bool:
        """Alt+` was pressed: start listening for 'Hey ULTRON'."""
        if not self.enabled:
            if not self.start():
                return False
        with self._lock:
            if self.state == AssistantState.ARMED:
                return True
            self._set_state(AssistantState.ARMED)
        self._start_wake_listening()
        return True

    def disarm(self):
        """Stop listening."""
        self._wake_running = False
        wake_word_detector.stop()
        if self.state not in (AssistantState.OFF, AssistantState.SPEAKING):
            self._set_state(AssistantState.DORMANT)

    def start_push_to_talk(self) -> bool:
        """Start a complete manual recording for the desktop hotkey."""
        if not self.enabled and not self.start():
            return False
        with self._lock:
            if self.state not in (AssistantState.DORMANT, AssistantState.ARMED):
                return False
            self._wake_running = False
            wake_word_detector.stop()
            self._manual_stop.clear()
            self._manual_text = None
            self._set_state(AssistantState.CAPTURING,
                            {"mode": "push_to_talk", "action": "started"})

        self._manual_thread = threading.Thread(
            target=self._manual_capture_worker,
            daemon=True,
            name="ultron-push-to-talk",
        )
        self._manual_thread.start()
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "push_to_talk_started", "mode": "desktop_hotkey"})
        return True

    def stop_push_to_talk(self) -> Optional[str]:
        """Stop the desktop hotkey recording, route its transcript, and return it."""
        if self.state != AssistantState.CAPTURING or not self._manual_thread:
            return None
        self._manual_stop.set()
        self._manual_thread.join(timeout=3.0)
        text = self._manual_text
        self._manual_thread = None
        if text:
            self.handle_transcript(text)
        else:
            self._set_state(AssistantState.DORMANT)
            audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.WARNING,
                      details={"action": "push_to_talk_empty"})
        return text

    def _manual_capture_worker(self):
        try:
            self._manual_text = self._record_and_transcribe(
                max_seconds=30.0,
                silence_ms=0,
                stop_event=self._manual_stop,
            )
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self._manual_text = None
            self._set_state(AssistantState.ERROR, {"error": self.error})

    def _start_wake_listening(self):
        if self._wake_running:
            return
        self._wake_running = True
        # Use one STT microphone stream. The bundled acoustic models are
        # surrogates for ULTRON and can race the keyword listener for input.
        threading.Thread(target=self._keyword_watch_loop, daemon=True,
                         name="ultron-kw-watch").start()
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "wake_listening_started", "mode": "stt_keyword"})

    # ------------------------------------------------------------------
    # command capture
    # ------------------------------------------------------------------
    def _open_mic(self):
        if self._pyaudio is None:
            import pyaudio
            self._pyaudio = pyaudio.PyAudio()
        import pyaudio
        return self._pyaudio.open(
            format=pyaudio.paInt16, channels=1, rate=16000,
            input=True, frames_per_buffer=512,
        )

    def _close_record_stream(self):
        if hasattr(self, "_rec_stream") and self._rec_stream:
            try:
                self._rec_stream.stop_stream()
                self._rec_stream.close()
            except Exception:
                pass
            self._rec_stream = None

    def capture_command(self, max_seconds: float = 6.0, silence_ms: int = 900):
        """Record the command that follows the wake word and run it."""
        with self._lock:
            if self.state != AssistantState.ARMED:
                return None
            wake_word_detector.stop()  # free the mic
            self._wake_running = False
            self._set_state(AssistantState.CAPTURING)

        def work():
            return self._record_and_transcribe(max_seconds, silence_ms)

        threading.Thread(target=lambda: self._capture_thread(work), daemon=True,
                         name="ultron-capture").start()
        return True

    def _capture_thread(self, work):
        try:
            text = work()
            if not text:
                audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.WARNING,
                          details={"action": "transcript_empty"})
                self._set_state(AssistantState.DORMANT)
                return
            self.handle_transcript(text)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self._set_state(AssistantState.ERROR, {"error": self.error})

    def handle_transcript(self, text: str, speak_reply: bool = True) -> Optional[str]:
        """Route a completed transcript through execution and optional TTS."""
        self.last_transcript = text
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "transcript", "text": text, "text_length": len(text)})
        if self.on_transcript:
            try:
                self.on_transcript(text)
            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                          details={"component": "assistant_transcript_callback", "error": str(e)})

        command = strip_wake_phrase(text)
        if not command:
            reply = "Yes? I'm listening. Say your command."
            self.last_response = reply
            audit_log(AuditEventType.VOICE_OUTPUT, severity=AuditSeverity.INFO,
                      details={"action": "empty_command", "response": reply})
            if speak_reply:
                self.speak(reply)
            return reply

        self._set_state(AssistantState.THINKING, {"command": command})
        audit_log(AuditEventType.MODEL_INFERENCE, severity=AuditSeverity.INFO,
                  details={"component": "voice_command", "command": command})
        reply = self._execute(command, transcript=text)
        self.last_response = reply
        audit_log(AuditEventType.VOICE_OUTPUT, severity=AuditSeverity.INFO,
                  details={"action": "command_response", "response": reply or ""})
        if self.on_response:
            try:
                self.on_response(reply or command)
            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                          details={"component": "assistant_response_callback", "error": str(e)})
        if reply and speak_reply:
            self._set_state(AssistantState.SPEAKING)
            self.speak(reply)
        self._set_state(AssistantState.DORMANT)
        return reply

    def _record_and_transcribe(
        self,
        max_seconds: float,
        silence_ms: int,
        stop_event: Optional[threading.Event] = None,
    ) -> Optional[str]:
        """Rolling record with silence stop, then faster-whisper transcription."""
        stream = self._open_mic()
        self._rec_stream = stream
        chunks: List[bytes] = []
        silent = 0
        started = time.time()
        drain_until: Optional[float] = None
        # enough lead-in audio for the wake word to be included
        while time.time() - started < max_seconds:
            if stop_event and stop_event.is_set():
                if drain_until is None:
                    # Let audio already inside the device/PortAudio buffer arrive.
                    drain_until = time.time() + 0.35
                elif time.time() >= drain_until:
                    break
            try:
                data = stream.read(512, exception_on_overflow=False)
            except Exception:
                break
            chunks.append(data)
            rms = _rms(np.frombuffer(data, dtype=np.int16))
            quiet = 0.02
            if rms < quiet:
                silent += 512
            else:
                silent = 0
            if len(chunks) > 16000 // 512 * 2 and silent * 1000 // 16000 >= silence_ms:
                break
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        self._rec_stream = None

        audio = b"".join(chunks)
        if not audio:
            return None
        return self._transcribe_bytes(audio)

    def _transcribe_bytes(self, audio: bytes) -> Optional[str]:
        if not self._stt:
            return None
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio)
            segments, _ = self._stt.transcribe(
                path, beam_size=3, language="en", vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
            )
            text = " ".join(s.text for s in segments).strip()
            return text or None
        except Exception as e:
            if any(marker in str(e).lower() for marker in ("cublas", "cudnn", "cuda")):
                audit_log(
                    AuditEventType.ERROR,
                    severity=AuditSeverity.WARNING,
                    details={"component": "assistant_stt",
                             "error": f"GPU runtime unavailable; retrying on CPU: {e}"},
                )
                try:
                    from ultron.voice.pipeline import voice_pipeline
                    voice_pipeline._load_stt(force_cpu=True)
                    self._stt = voice_pipeline._stt_model
                    if self._stt:
                        segments, _ = self._stt.transcribe(
                            path, beam_size=3, language="en", vad_filter=True,
                            vad_parameters=dict(min_silence_duration_ms=300),
                        )
                        text = " ".join(s.text for s in segments).strip()
                        audit_log(
                            AuditEventType.VOICE_INPUT,
                            severity=AuditSeverity.INFO,
                            details={"action": "transcribe", "text_length": len(text),
                                     "device": "cpu"},
                        )
                        return text or None
                except Exception as cpu_error:
                    audit_log(
                        AuditEventType.ERROR,
                        severity=AuditSeverity.ERROR,
                        details={"component": "assistant_stt_cpu_fallback",
                                 "error": str(cpu_error)},
                    )
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                      details={"component": "assistant_stt", "error": str(e)})
            return None
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass

    def _keyword_watch_loop(self):
        """Safety net: transcribe and accept when the word 'ultron' is spoken.

        Covers cases where the acoustic surrogate does not fire.
        """
        window = 1.3  # seconds of audio per transcription window
        while self._wake_running:
            try:
                if not self._pyaudio:
                    import pyaudio
                    self._pyaudio = pyaudio.PyAudio()
                import pyaudio
                stream = self._pyaudio.open(
                    format=pyaudio.paInt16, channels=1, rate=16000,
                    input=True, frames_per_buffer=512,
                )
                frames = []
                deadline = time.time() + window
                while time.time() < deadline:
                    try:
                        frames.append(stream.read(512, exception_on_overflow=False))
                    except Exception:
                        break
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
                audio = b"".join(frames)
                if audio:
                    text = self._transcribe_bytes(audio)
                    if text:
                        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                                  details={"action": "wake_window_transcript", "text": text[:120]})
                    if text and is_wake_phrase(text) and self.state == AssistantState.ARMED:
                        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                                  details={"action": "keyword_wake", "text": text[:60]})
                        if strip_wake_phrase(text):
                            # The command may already be in the same STT window.
                            self._set_state(AssistantState.THINKING,
                                            {"command": strip_wake_phrase(text)})
                            self.handle_transcript(text)
                        else:
                            self.capture_command()
                        return
            except Exception as e:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                          details={"component": "keyword_watch", "error": str(e)})
                time.sleep(1.0)

    # ------------------------------------------------------------------
    # execution + speech
    # ------------------------------------------------------------------
    def _execute(self, command: str, transcript: Optional[str] = None) -> Optional[str]:
        """Classify voice input, normalize commands, and route it to the kernel."""
        original_text = transcript or command
        is_command, prepared_text = self._prepare_voice_input(original_text)
        command = prepared_text or command
        if is_command and command == original_text.strip():
            command = self._normalize_voice_command(command) or command
        try:
            from ultron.kernel import kernel, UltronRequest, RequestType
            from ultron.workspace.selector import workspace_manager
            kernel.initialize()
            req = UltronRequest(
                request_id=f"voice_{int(time.time()*1000)}",
                request_type=RequestType.VOICE_COMMAND,
                input_text=command,
                workspace_id=workspace_manager.get_current_workspace_id(),
            )
            resp = kernel.process(req)
            text = getattr(resp, "response_text", None) or str(resp)
            if getattr(resp, "success", False):
                return text.strip()
            if not is_command:
                return text.strip()
            fallback = self._direct_tool_dispatch(command)
            return fallback or text.strip()
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"component": "assistant_exec", "error": str(e)})
            # fall back to a direct tool call if the kernel can't route it
            if not is_command:
                return f"Sorry, I could not answer that. {type(e).__name__}: {e}"
            try:
                return self._direct_tool_dispatch(command)
            except Exception as e2:
                return f"Sorry, I could not do that. {type(e2).__name__}: {e2}"

    def _prepare_voice_input(self, transcript: str) -> tuple[bool, str]:
        """Classify input before applying command-only speech correction."""
        text = transcript.strip()
        try:
            from ultron.model.loader import model_manager
            if model_manager.is_loaded():
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "Classify the user's speech as exactly one type: COMMAND or QUESTION. "
                            "A COMMAND asks ULTRON to perform an action, such as opening an app, "
                            "creating or changing a file, or running a task. A QUESTION asks for "
                            "information, explanation, status, or a file search. "
                            "Return exactly two lines:\n"
                            "TYPE: COMMAND or TYPE: QUESTION\n"
                            "TEXT: <text>\n"
                            "If COMMAND, correct only obvious speech-to-text errors and app names. "
                            "Make the command explicit and concise without inventing actions, "
                            "targets, paths, or details. If QUESTION, copy the user's question "
                            "unchanged after TEXT. Do not answer the question."
                        ),
                    },
                    {"role": "user", "content": text},
                ]
                result = model_manager.chat_completion(
                    messages, max_tokens=96, temperature=0.0,
                ).strip()
                lines = [line.strip() for line in result.splitlines() if line.strip()]
                kind = next(
                    (line.split(":", 1)[1].strip().upper()
                     for line in lines if line.upper().startswith("TYPE:")),
                    "",
                )
                prepared = next(
                    (line.split(":", 1)[1].strip()
                     for line in lines if line.upper().startswith("TEXT:")),
                    "",
                )
                if kind in {"COMMAND", "QUESTION"} and prepared:
                    is_command = kind == "COMMAND"
                    audit_log(
                        AuditEventType.MODEL_INFERENCE,
                        severity=AuditSeverity.INFO,
                        details={
                            "component": "voice_input_classification",
                            "input": text,
                            "type": kind,
                            "prepared": prepared,
                        },
                    )
                    return is_command, prepared
        except Exception as e:
            audit_log(
                AuditEventType.ERROR,
                severity=AuditSeverity.WARNING,
                details={"component": "voice_input_classification", "error": str(e)},
            )

        return self._fallback_voice_classification(text)

    @staticmethod
    def _fallback_voice_classification(text: str) -> tuple[bool, str]:
        """Keep routing safe when the local model cannot classify the input."""
        normalized = text.lower().rstrip(" .!?")
        question_starts = (
            "what ", "why ", "when ", "where ", "who ", "how ",
            "can you tell me", "do you know", "is ", "are ", "will ",
        )
        command_starts = (
            "open ", "launch ", "start ", "close ", "create ", "make ",
            "write ", "read ", "show ", "find ", "search ", "run ",
            "delete ", "move ", "copy ", "set ", "turn ",
        )
        if text.rstrip().endswith("?") or normalized.startswith(question_starts):
            return False, text
        if normalized.startswith(command_starts):
            return True, text
        return False, text

    def _normalize_voice_command(self, transcript: str) -> Optional[str]:
        """Ask the local LLM to correct STT wording without inventing actions."""
        try:
            from ultron.model.loader import model_manager
            if not model_manager.is_loaded():
                return transcript.strip()
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a command transcription corrector. "
                        "Correct only spelling, speech-to-text mistakes, and unclear wording "
                        "when the intended meaning is obvious. Correct application names and "
                        "aliases to their standard names, including Notepad, Chrome, VS Code, "
                        "Calculator, Explorer, Windows Terminal, CMD, and Paint. "
                        "Preserve the user's exact intent. "
                        "Do not add, remove, or invent actions, targets, or details. "
                        "Return only the corrected command as one plain text line. "
                        "Do not explain, apologize, greet, quote, format, or add punctuation."
                    ),
                },
                {"role": "user", "content": transcript},
            ]
            normalized = model_manager.chat_completion(
                messages, max_tokens=48, temperature=0.0,
            ).strip().strip('`').strip()
            if not normalized or normalized.lower().startswith("error:"):
                return transcript.strip()
            audit_log(AuditEventType.MODEL_INFERENCE, severity=AuditSeverity.INFO,
                      details={"component": "voice_normalization", "input": transcript,
                               "normalized": normalized})
            return normalized
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                      details={"component": "voice_normalization", "error": str(e)})
            return transcript.strip()

    def _direct_tool_dispatch(self, command: str) -> str:
        """Minimal direct routing when the kernel is unavailable."""
        import shutil
        import subprocess
        m = re.search(r"\b(?:open|launch|start)\s+([\w\s\.\-]+)$", command.strip(), re.I)
        if m:
            app = m.group(1).strip().rstrip(".")
            common = {
                "notepad": "notepad.exe", "chrome": "chrome.exe", "calculator": "calc.exe",
                "google chrome": "chrome.exe",
                "explorer": "explorer.exe", "cmd": "cmd.exe", "paint": "mspaint.exe",
                "vs code": "code.exe", "vscode": "code.exe", "visual studio code": "code.exe",
                "windows terminal": "wt.exe", "terminal": "wt.exe",
            }
            target = common.get(app.lower(), f"{app}.exe")
            if not shutil.which(target):
                known_paths = {
                    "chrome.exe": [
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                        os.path.expandvars(
                            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
                        ),
                    ],
                    "code.exe": [
                        r"C:\Program Files\Microsoft VS Code\Code.exe",
                        os.path.expandvars(
                            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"
                        ),
                    ],
                }
                executable = next(
                    (path for path in known_paths.get(target, []) if os.path.exists(path)),
                    target,
                )
            else:
                executable = shutil.which(target)
            subprocess.Popen([executable])
            audit_log(AuditEventType.APP_LAUNCHED, severity=AuditSeverity.INFO,
                      details={"app": app, "executable": executable, "source": "voice"})
            return f"Opening {app}."
        return None

    def speak(self, text: str, block: bool = False) -> bool:
        """Speak text aloud (fallback: Windows SAPI)."""
        if not text:
            return False
        if block or threading.current_thread() is threading.main_thread():
            return self._speak_sync(text)
        t = threading.Thread(target=self._speak_sync, args=(text,), daemon=True)
        self._speak_threads.append(t)
        t.start()
        return True

    def _speak_sync(self, text: str) -> bool:
        try:
            if self._tts is not None:
                chunks = list(self._tts.synthesize(text))
                self._play_audio(b"".join(c.audio_int16_bytes for c in chunks))
                return True
            # TTS not loaded - try loading then fall back
            from ultron.voice.pipeline import voice_pipeline
            voice_pipeline.initialize()
            self._tts = voice_pipeline._tts_voice
            if self._tts is not None:
                chunks = list(self._tts.synthesize(text))
                self._play_audio(b"".join(c.audio_int16_bytes for c in chunks))
                return True
            return self._speak_sapi(text)
        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                      details={"component": "assistant_tts", "error": str(e)})
            return self._speak_sapi(text)

    def _play_audio(self, audio: bytes):
        import pyaudio
        pa = self._pyaudio or pyaudio.PyAudio()
        self._pyaudio = pa
        stream = pa.open(format=pa.get_format_from_width(2), channels=1,
                         rate=22050, output=True)
        try:
            for i in range(0, len(audio), 8192):
                stream.write(audio[i:i + 8192])
        finally:
            stream.stop_stream()
            stream.close()

    def _speak_sapi(self, text: str) -> bool:
        try:
            import win32com.client  # noqa
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak(text)
            return True
        except Exception:
            try:
                import subprocess
                safe = text[:400].replace("'", "''")
                ps = (
                    "Add-Type -AssemblyName System.Speech;"
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                    f"$s.Speak('{safe}')"
                )
                subprocess.Popen(["powershell", "-Command", ps],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return True
            except Exception:
                return False

    # ------------------------------------------------------------------
    # state plumbing
    # ------------------------------------------------------------------
    def _set_state(self, state: AssistantState, data: Optional[Dict[str, Any]] = None):
        self.state = state
        if self.on_state_change:
            try:
                self.on_state_change(state, data or {})
            except Exception:
                pass


def _rms(data) -> float:
    if len(data) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(data.astype(np.float64)))) / 32768.0)


# Global singleton
voice_assistant = VoiceAssistant()
