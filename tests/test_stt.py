"""Test STT with silence"""
import sys
from pathlib import Path
import wave
import struct

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultron.voice.pipeline import voice_pipeline


def test_stt():
    print("Testing voice pipeline...")
    voice_pipeline.initialize()

    # Create a simple test WAV with silence
    sample_rate = 16000
    duration = 1.0
    samples = [0] * int(sample_rate * duration)
    with wave.open('test_silence.wav', 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for s in samples:
            wf.writeframes(struct.pack('<h', s))

    print('Test WAV created')

    # Read it back and transcribe
    with open('test_silence.wav', 'rb') as f:
        audio_data = f.read()

    print(f'Audio data length: {len(audio_data)} bytes')

    # Test STT
    text = voice_pipeline._transcribe_audio(audio_data)
    print(f'STT Result: "{text}"')

    import os
    os.remove('test_silence.wav')


if __name__ == "__main__":
    test_stt()