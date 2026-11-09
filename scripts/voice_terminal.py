"""Interactive terminal push-to-talk voice loop with stage logging."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultron.security.audit import audit_log, AuditEventType, AuditSeverity
from ultron.voice.assistant import voice_assistant
from ultron.voice.pipeline import voice_pipeline


def main():
    print("=== ULTRON terminal voice test ===", flush=True)
    print("Loading STT/TTS...", flush=True)
    voice_pipeline.initialize()
    if not voice_pipeline.is_ready():
        print("FAIL: STT model did not load. Check logs.", flush=True)
        return 1
    voice_pipeline.start(lambda text: print(f"TRANSCRIPT: {text}", flush=True))
    voice_assistant.start()
    print("PASS: voice engine ready", flush=True)
    print("Press Enter, speak your command, then press Enter again.", flush=True)
    input("START listening > ")
    audit_log(AuditEventType.VOICE_INPUT, severity=AuditSeverity.INFO,
              details={"action": "terminal_push_to_talk_start"})
    if not voice_pipeline.push_to_talk_start():
        print("FAIL: microphone recording did not start. Check logs.", flush=True)
        voice_pipeline.stop()
        return 1
    input("STOP listening > ")
    text = voice_pipeline.push_to_talk_stop()
    if not text:
        print("FAIL: no transcript produced. Check microphone input and logs.", flush=True)
        voice_pipeline.stop()
        return 1
    print(f"TRANSCRIPT: {text}", flush=True)
    reply = voice_assistant.handle_transcript(text)
    print(f"RESPONSE: {reply or '(no response)'}", flush=True)
    print("PASS: transcript routed to command execution and TTS", flush=True)
    voice_pipeline.stop()
    voice_assistant.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())