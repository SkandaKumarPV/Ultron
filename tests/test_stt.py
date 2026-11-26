import io
import wave
from unittest.mock import Mock, patch

import pytest
import requests

from ultron.voice.pipeline import VoicePipeline, VoiceState
from ultron.voice.stt import (
    API_KEY_ENV, CARTESIA_KEY_ENV, DEEPGRAM_KEY_ENV, TranscriptionError, has_api_key, transcribe_pcm,
)


@pytest.fixture(autouse=True)
def isolate_project_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(CARTESIA_KEY_ENV, raising=False)
    monkeypatch.delenv(DEEPGRAM_KEY_ENV, raising=False)
    monkeypatch.setattr("ultron.voice.stt.PROJECT_DIR", tmp_path)


def test_push_to_talk_uploads_valid_wav_and_routes_transcript(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    pcm = b"\x01\x00" * 1600
    pipeline = VoicePipeline()
    callback = Mock()
    pipeline.start(callback)
    pipeline._state = VoiceState.LISTENING
    response = Mock(status_code=200)
    response.json.return_value = {"text": "  Open Notepad.  "}

    with patch.object(pipeline, "_stop_recording", return_value=pcm), patch(
        "ultron.voice.stt.requests.post", return_value=response
    ) as post:
        assert pipeline.push_to_talk_stop() == "Open Notepad."

    kwargs = post.call_args.kwargs
    assert kwargs["headers"] == {"xi-api-key": "test-key"}
    assert kwargs["data"]["model_id"] == "scribe_v3"
    assert kwargs["allow_redirects"] is False
    with wave.open(io.BytesIO(kwargs["files"]["file"][1]), "rb") as recording:
        assert (recording.getnchannels(), recording.getframerate(), recording.getsampwidth()) == (1, 16000, 2)
        assert recording.readframes(recording.getnframes()) == pcm
    callback.assert_called_once_with("Open Notepad.")
    assert pipeline.state == VoiceState.IDLE


def test_missing_key_stops_before_upload(monkeypatch, tmp_path):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setattr("ultron.voice.stt.PROJECT_DIR", tmp_path)
    with patch("ultron.voice.stt.requests.post") as post:
        with pytest.raises(TranscriptionError, match=API_KEY_ENV):
            transcribe_pcm(b"\0\0", 16000, 1)
    post.assert_not_called()


def test_project_env_key_is_loaded_with_environment_override(monkeypatch, tmp_path):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setattr("ultron.voice.stt.PROJECT_DIR", tmp_path)
    (tmp_path / ".env").write_text(f"{API_KEY_ENV}=project-key\n", encoding="utf-8")
    assert has_api_key()

    response = Mock(status_code=200)
    response.json.return_value = {"text": "hello"}
    with patch("ultron.voice.stt.requests.post", return_value=response) as post:
        transcribe_pcm(b"\0\0", 16000, 1)
        assert post.call_args.kwargs["headers"]["xi-api-key"] == "project-key"

        monkeypatch.setenv(API_KEY_ENV, "environment-key")
        transcribe_pcm(b"\0\0", 16000, 1)
        assert post.call_args.kwargs["headers"]["xi-api-key"] == "environment-key"


@pytest.mark.parametrize(("status", "message"), [(401, "API key"), (429, "rate limit"), (503, "HTTP 503")])
def test_provider_error_restores_idle_without_routing(monkeypatch, status, message):
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    pipeline = VoicePipeline()
    callback = Mock()
    pipeline.start(callback)
    pipeline._state = VoiceState.LISTENING
    with patch.object(pipeline, "_stop_recording", return_value=b"\0\0"), patch(
        "ultron.voice.stt.requests.post", return_value=Mock(status_code=status)
    ):
        with pytest.raises(TranscriptionError, match=message):
            pipeline.push_to_talk_stop()
    assert pipeline.state == VoiceState.IDLE
    callback.assert_not_called()


def test_timeout_has_a_safe_error_message(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    with patch("ultron.voice.stt.requests.post", side_effect=requests.Timeout("test-key")):
        with pytest.raises(TranscriptionError, match="timed out") as error:
            transcribe_pcm(b"\0\0", 16000, 1)
    assert "test-key" not in str(error.value)


def test_cartesia_uses_its_own_key_when_both_are_configured(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "eleven-key")
    monkeypatch.setenv(CARTESIA_KEY_ENV, "cartesia-key")
    response = Mock(status_code=200)
    response.json.return_value = {"text": "Hello ULTRON."}
    with patch("ultron.voice.stt.requests.post", return_value=response) as post:
        assert transcribe_pcm(b"\x01\x00", 16000, 1) == "Hello ULTRON."
    assert post.call_args.args == ("https://api.cartesia.ai/stt",)
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer cartesia-key"
    assert post.call_args.kwargs["data"]["model"] == "ink-whisper"


def test_elevenlabs_key_id_gets_a_specific_error(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "key-id")
    response = Mock(status_code=400)
    response.json.return_value = {"detail": {"status": "api_key_id_used_as_api_key"}}
    with patch("ultron.voice.stt.requests.post", return_value=response):
        with pytest.raises(TranscriptionError, match="received a key ID"):
            transcribe_pcm(b"\x01\x00", 16000, 1)


def test_deepgram_prefers_its_key_and_uploads_the_wav(monkeypatch, tmp_path):
    monkeypatch.setenv(API_KEY_ENV, "eleven-key")
    monkeypatch.setenv(CARTESIA_KEY_ENV, "cartesia-key")
    (tmp_path / ".env").write_text(f"{DEEPGRAM_KEY_ENV}=deepgram-key\n", encoding="utf-8")
    response = Mock(status_code=200)
    response.json.return_value = {"results": {"channels": [{"alternatives": [{"transcript": " Hey ULTRON. "}]}]}}
    pcm = b"\x01\x00" * 100
    with patch("ultron.voice.stt.requests.post", return_value=response) as post:
        assert transcribe_pcm(pcm, 44100, 1) == "Hey ULTRON."
    assert post.call_args.args == ("https://api.deepgram.com/v1/listen",)
    request = post.call_args.kwargs
    assert request["headers"] == {"Authorization": "Token deepgram-key", "Content-Type": "audio/wav"}
    assert request["params"]["model"] == "nova-3"
    assert request["allow_redirects"] is False
    with wave.open(io.BytesIO(request["data"]), "rb") as recording:
        assert recording.getframerate() == 44100
        assert recording.readframes(recording.getnframes()) == pcm
