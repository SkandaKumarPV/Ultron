"""Test voice pipeline push-to-talk"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultron.voice.pipeline import voice_pipeline


def test_voice():
    print("Initializing voice pipeline...")
    voice_pipeline.initialize()
    print(f"Ready: {voice_pipeline.is_ready()}")

    received = []

    def on_text(text):
        received.append(text)
        print(f"[TRANSCRIBED]: {text}")

    voice_pipeline.start(on_text)

    print("\n=== Push-to-Talk Test ===")
    print("Press Enter to START recording...")
    input()
    
    if voice_pipeline.push_to_talk_start():
        print("Recording... Speak now!")
        print("Press Enter to STOP recording...")
        input()
        
        text = voice_pipeline.push_to_talk_stop()
        print(f"Result: {text}")
    else:
        print("Failed to start recording")

    voice_pipeline.stop()
    
    # Test TTS
    print("\n=== TTS Test ===")
    voice_pipeline.start(lambda x: None)
    voice_pipeline.speak("Voice system test complete.")
    voice_pipeline.stop()
    print("TTS test done.")


if __name__ == "__main__":
    test_voice()