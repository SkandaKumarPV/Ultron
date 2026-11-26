"""ULTRON Voice Assistant - Alexa-style voice control

Flow (like Alexa):
  1. Press Alt+` (or the mic button) -> assistant becomes ARMED
  2. Say "Hey ULTRON" + your command, e.g. "Hey ULTRON open notepad"
  3. ULTRON verifies its own name, runs the command through the kernel,
     and SPEAKS the answer back out loud.

Uses ElevenLabs for speech recognition and Piper or Windows SAPI for speech output.
While wake listening is enabled, detected speech is sent for wake-phrase verification.
"""
import re
import time
import threading
from collections import deque
from queue import Empty, Full, Queue
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

import numpy as np

from ultron.config import config
from ultron.automation import command_text, explicit_action, wants_workspace
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity
from ultron.voice.stt import TranscriptionError, transcribe_pcm
from ultron.voice.wake_word import wake_word_detector

_WAKE_PREFIX = re.compile(
    r"^(?:(?:hey|hi|hello|okay|ok)[\s,]+)?"
    r"(?:ultron|al[\s-]+tron|altar on|ultra one|ultra on|hail\s*tron)\b", re.I
)


class AssistantState(Enum):
    OFF = "off"              # engine off
    DORMANT = "dormant"      # running, waiting to be armed
    ARMED = "armed"          # hotkey pressed - listening for wake word
    CAPTURING = "capturing"  # wake word heard - recording the command
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"    # running the command
    SPEAKING = "speaking"    # speaking the reply
    ERROR = "error"


def strip_wake_phrase(text: str) -> str:
    """Remove the wake phrase (and any leading greeting) so only the command remains."""
    if not text:
        return ""
    text = text.strip()
    match = _WAKE_PREFIX.match(text)
    if match:
        return _strip_leading_greeting(text[match.end():].strip(" ,.!?:;"))
    return text


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
    return bool(_WAKE_PREFIX.match(text.strip()))


class VoiceAssistant:
    """Manages the voice-interaction loop."""

    def __init__(self):
        self.state = AssistantState.OFF
        self.enabled = False
        self._lock = threading.RLock()
        self._wake_stop = threading.Event()
        self._continuous_wake = False
        self._pyaudio = None
        self._record_lock = threading.Lock()
        self.input_device = None
        self._tts = None
        self._speak_threads: List[threading.Thread] = []
        self._speech_lock = threading.Lock()
        self._manual_stop = threading.Event()
        self._manual_thread: Optional[threading.Thread] = None
        self._manual_text: Optional[str] = None
        self._manual_speak_reply = True
        self.last_transcript: Optional[str] = None
        self.last_response: Optional[str] = None
        self.error: Optional[str] = None
        self.reply_aloud = True
        # UI callbacks
        self.on_state_change: Optional[Callable[[AssistantState, Dict[str, Any]], None]] = None
        self.on_transcript: Optional[Callable[[str], None]] = None
        self.on_response: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def initialize(self) -> bool:
        """Check voice input configuration and load TTS."""
        try:
            from ultron.voice.pipeline import voice_pipeline
            voice_pipeline.initialize()
            if (error := voice_pipeline.input_error()) is not None:
                raise TranscriptionError(error)
            self._tts = voice_pipeline._tts_voice
            self.error = None
            return True
        except Exception as e:
            self._set_input_error(e)
            return False

    def start(self, listen_for_wake: bool = True) -> bool:
        """Turn on voice input and begin listening for the wake phrase."""
        if self.enabled:
            return self.arm() if listen_for_wake else True
        if not self.initialize():
            return False
        self.enabled = True
        self._set_state(AssistantState.DORMANT)
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "assistant_start"})
        return self.arm() if listen_for_wake else True

    def stop(self):
        """Turn the assistant OFF."""
        self.enabled = False
        self._continuous_wake = False
        self._wake_stop.set()
        self._manual_stop.set()
        wake_word_detector.stop()
        self._set_state(AssistantState.OFF)
        audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
                  details={"action": "assistant_stop"})

    # ------------------------------------------------------------------
    # arming (hotkey / mic button)
    # ------------------------------------------------------------------
    def arm(self) -> bool:
        """Start listening for 'Hey ULTRON' until voice mode is paused."""
        if not self.enabled:
            if not self.start(listen_for_wake=False):
                return False
        with self._lock:
            if self.state == AssistantState.ARMED:
                return True
            if self.state not in (AssistantState.DORMANT, AssistantState.ERROR):
                return False
            self.error = None
            self._continuous_wake = True
            self._set_state(AssistantState.ARMED)
            self._start_wake_listening()
        return True

    def disarm(self):
        """Stop listening."""
        self._continuous_wake = False
        self._wake_stop.set()
        wake_word_detector.stop()
        if self.state not in (AssistantState.OFF, AssistantState.SPEAKING):
            self._set_state(AssistantState.DORMANT)

    def start_push_to_talk(self, speak_reply: bool = True) -> bool:
        """Start a complete manual recording for the desktop hotkey."""
        if not self.enabled and not self.start(listen_for_wake=False):
            return False
        with self._lock:
            if self._manual_thread and self._manual_thread.is_alive():
                return False
            if self.state not in (AssistantState.DORMANT, AssistantState.ARMED, AssistantState.ERROR):
                return False
            self._wake_stop.set()
            wake_word_detector.stop()
            self._manual_stop.clear()
            self._manual_text = None
            self._manual_speak_reply = speak_reply
            self.error = None
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

    def stop_push_to_talk(self, speak_reply: Optional[bool] = None) -> Optional[str]:
        """Release the microphone and wait for the current transcript."""
        worker = self._manual_thread
        if worker is None:
            return None
        if speak_reply is not None and self.state == AssistantState.CAPTURING:
            self._manual_speak_reply = speak_reply
        self._manual_stop.set()
        worker.join(timeout=28.0)
        return self._manual_text

    def _manual_capture_worker(self):
        try:
            text = self._record_and_transcribe(
                max_seconds=30.0, silence_ms=0, stop_event=self._manual_stop,
            )
            if not self.enabled or self.state not in (AssistantState.CAPTURING, AssistantState.TRANSCRIBING):
                return
            self._manual_text = text
            if text:
                if self._manual_speak_reply:
                    self.handle_transcript(text)
                else:
                    self.handle_transcript(text, speak_reply=False)
            if self.state in (AssistantState.CAPTURING, AssistantState.TRANSCRIBING):
                self._set_state(AssistantState.DORMANT)
                if not text:
                    audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.WARNING,
                              details={"action": "push_to_talk_empty"})
            self._resume_wake()
        except Exception as error:
            if self.enabled and self.state != AssistantState.OFF:
                self._set_input_error(error)

    def _set_input_error(self, error):
        self.error = str(error)
        self._set_state(AssistantState.ERROR, {"error": self.error})
        audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                  details={"component": "voice_input", "message": self.error})

    def _start_wake_listening(self):
        stop_event = threading.Event()
        self._wake_stop = stop_event
        threading.Thread(target=self._listen_for_wake, args=(stop_event,), daemon=True,
                         name="ultron-wake-listen").start()

    def _listen_for_wake(self, stop_event: threading.Event):
        try:
            while not stop_event.is_set():
                text = self._record_and_transcribe(
                    max_seconds=12.0, silence_ms=850, stop_event=stop_event, armed=True,
                )
                if stop_event.is_set() or self.state != AssistantState.ARMED:
                    return
                if text and is_wake_phrase(text):
                    if strip_wake_phrase(text):
                        self.handle_transcript(text)
                        self._resume_wake()
                    else:
                        self.capture_command()
                    return
                if not self._continuous_wake:
                    self._set_state(AssistantState.DORMANT)
                    return
        except Exception as error:
            if not stop_event.is_set():
                self._set_input_error(error)

    def _resume_wake(self):
        if self.enabled and self._continuous_wake and self.state == AssistantState.DORMANT:
            self.arm()

    # ------------------------------------------------------------------
    # command capture
    # ------------------------------------------------------------------
    @staticmethod
    def _microphone_devices(audio):
        for index in range(audio.get_device_count()):
            device = audio.get_device_info_by_index(index)
            if device["maxInputChannels"] > 0:
                host = audio.get_host_api_info_by_index(device["hostApi"])["name"]
                yield (device["name"], host), device

    @classmethod
    def microphone_choices(cls):
        import pyaudio
        audio = pyaudio.PyAudio()
        try:
            return [key for key, _ in cls._microphone_devices(audio)
                    if "WDM-KS" not in key[1] and "sound capture driver" not in key[0].lower()
                    and "sound mapper" not in key[0].lower()]
        finally:
            audio.terminate()

    def _open_mic(self, callback):
        if self._pyaudio is None:
            import pyaudio
            self._pyaudio = pyaudio.PyAudio()
        import pyaudio
        if self.input_device is None:
            device = self._pyaudio.get_default_input_device_info()
        else:
            device = next((info for key, info in self._microphone_devices(self._pyaudio)
                           if key == self.input_device), None)
            if device is None:
                raise RuntimeError("The selected microphone is unavailable. Choose another input in the Voice tab.")
        rate = int(device["defaultSampleRate"])
        stream = self._pyaudio.open(
            format=pyaudio.paInt16, channels=1, rate=rate, input=True,
            input_device_index=device["index"], frames_per_buffer=max(256, rate // 32),
            stream_callback=callback,
        )
        return stream, rate

    def test_microphone(self):
        """Measure two seconds of local audio without saving or transcribing it."""
        import pyaudio
        if not self._record_lock.acquire(timeout=1.0):
            raise RuntimeError("Voice input is busy. Try again after recording ends.")
        buffers = Queue(maxsize=64)

        def receive(data, frames, clock, status):
            if data:
                try:
                    buffers.put_nowait(data)
                except Full:
                    pass
            return None, pyaudio.paContinue

        try:
            stream, _ = self._open_mic(receive)
            try:
                deadline = time.monotonic() + 2.0
                count, peak = 0, 0
                while time.monotonic() < deadline:
                    try:
                        data = buffers.get(timeout=0.1)
                    except Empty:
                        continue
                    samples = np.frombuffer(data, dtype=np.int16).astype(np.int32)
                    count += samples.size
                    peak = max(peak, int(np.abs(samples).max()))
                return count, peak / 32768
            finally:
                stream.close()
        finally:
            self._record_lock.release()

    def capture_command(self, max_seconds: float = 6.0, silence_ms: int = 900):
        """Record the command that follows the wake word and run it."""
        with self._lock:
            if self.state != AssistantState.ARMED:
                return None
            wake_word_detector.stop()  # free the mic
            self._wake_stop.set()
            self._set_state(AssistantState.CAPTURING)

        def work():
            return self._record_and_transcribe(max_seconds, silence_ms)

        threading.Thread(target=lambda: self._capture_thread(work), daemon=True,
                         name="ultron-capture").start()
        return True

    def _capture_thread(self, work):
        try:
            text = work()
            if not self.enabled or self.state not in (AssistantState.CAPTURING, AssistantState.TRANSCRIBING):
                return
            if not text:
                audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.WARNING,
                          details={"action": "transcript_empty"})
                self._set_state(AssistantState.DORMANT)
                self._resume_wake()
                return
            self.handle_transcript(text)
            self._resume_wake()
        except Exception as e:
            if self.enabled and self.state != AssistantState.OFF:
                self._set_input_error(e)

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
                self.speak(reply, block=True)
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
            self.speak(reply, block=True)
        if self.state == AssistantState.THINKING:
            self._set_state(AssistantState.DORMANT)
        return reply

    def _record_and_transcribe(
        self,
        max_seconds: float,
        silence_ms: int,
        stop_event: Optional[threading.Event] = None,
        armed: bool = False,
    ) -> Optional[str]:
        with self._record_lock:
            if not self.enabled or (armed and stop_event and stop_event.is_set()):
                return None
            import pyaudio
            buffers = Queue(maxsize=64)

            def receive(data, frames, clock, status):
                if data:
                    try:
                        buffers.put_nowait(data)
                    except Full:
                        pass
                return None, pyaudio.paContinue

            stream, sample_rate = self._open_mic(receive)
            chunks: List[bytes] = []
            lead_in = deque(maxlen=8)
            silent_frames = 0
            heard_speech = False
            deadline = time.monotonic() + max_seconds
            last_buffer = time.monotonic()
            drain_until: Optional[float] = None
            try:
                while self.enabled and time.monotonic() < deadline:
                    if stop_event and stop_event.is_set():
                        if armed:
                            return None
                        if drain_until is None:
                            drain_until = time.monotonic() + 0.35
                        elif time.monotonic() >= drain_until:
                            break
                    try:
                        data = buffers.get(timeout=0.2)
                    except Empty:
                        if time.monotonic() - last_buffer > 2:
                            raise RuntimeError("The microphone opened but sent no audio. Check Windows microphone access and the default input device.")
                        continue
                    last_buffer = time.monotonic()
                    threshold = 0.02 if armed else 0.005
                    voiced = _rms(np.frombuffer(data, dtype=np.int16)) >= threshold
                    if armed and not heard_speech:
                        if not voiced:
                            lead_in.append(data)
                            continue
                        chunks.extend(lead_in)
                        deadline = time.monotonic() + max_seconds
                    chunks.append(data)
                    if voiced:
                        heard_speech = True
                        silent_frames = 0
                    else:
                        silent_frames += len(data) // 2
                    if silence_ms and heard_speech and silent_frames * 1000 >= silence_ms * sample_rate:
                        break
            finally:
                stream.close()

        if not self.enabled or not heard_speech:
            return None
        if armed and stop_event and stop_event.is_set():
            return None
        if not armed and self.state == AssistantState.CAPTURING:
            self._set_state(AssistantState.TRANSCRIBING)
        return self._transcribe_bytes(b"".join(chunks), sample_rate)

    def _transcribe_bytes(self, audio: bytes, sample_rate: int = 16000) -> Optional[str]:
        return transcribe_pcm(audio, sample_rate=sample_rate, channels=1)

    # ------------------------------------------------------------------
    # execution + speech
    # ------------------------------------------------------------------
    def _execute(self, command: str, transcript: Optional[str] = None) -> Optional[str]:
        """Classify voice input, normalize commands, and route it to the kernel."""
        command = command_text(command)
        original_text = command_text(strip_wake_phrase(transcript)) if transcript else command
        has_route = bool(explicit_action(command)) or wants_workspace(command)
        is_command = False
        if not has_route:
            is_command, prepared_text = self._prepare_voice_input(original_text)
            command = prepared_text or command
            if is_command and command == original_text.strip():
                command = self._normalize_voice_command(command) or command
            corrected_action = explicit_action(command)
            if corrected_action and (corrected_action[0] == "ask_in_browser" or corrected_action[1].get("args")):
                # Model correction cannot create a new external request.
                command = original_text
        try:
            from ultron.kernel import kernel, UltronRequest, RequestType
            from ultron.workspace.selector import workspace_manager
            kernel.initialize(start_voice=False)
            req = UltronRequest(
                request_id=f"voice_{int(time.time()*1000)}",
                request_type=RequestType.VOICE_COMMAND,
                input_text=command,
                workspace_id=workspace_manager.get_current_workspace_id(),
            )
            resp = kernel.process(req)
            text = getattr(resp, "response_text", None) or str(resp)
            return text.strip()
        except Exception as error:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      details={"component": "assistant_exec", "error": str(error)})
            return f"Sorry, I could not complete that request. {error}"

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

    def speak(self, text: str, block: bool = False) -> bool:
        """Speak text aloud (fallback: Windows SAPI)."""
        if not text:
            return False
        if block or threading.current_thread() is threading.main_thread():
            return self._speak_with_state(text)
        t = threading.Thread(target=self._speak_with_state, args=(text,), daemon=True)
        self._speak_threads.append(t)
        t.start()
        return True

    def _speak_with_state(self, text: str) -> bool:
        with self._speech_lock:
            previous = self.state
            self._set_state(AssistantState.SPEAKING)
            try:
                return self._speak_sync(text)
            finally:
                if self.state == AssistantState.SPEAKING:
                    self._set_state(previous)

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
                ps = (
                    "Add-Type -AssemblyName System.Speech;"
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                    "[Console]::InputEncoding = [Text.Encoding]::UTF8;"
                    "$s.Speak([Console]::In.ReadToEnd())"
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=120, input=text, encoding="utf-8",
                )
                return result.returncode == 0
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
