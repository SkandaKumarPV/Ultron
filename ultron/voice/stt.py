"""Hosted transcription for recorded 16-bit PCM audio."""

import io
import os
import ssl
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from dotenv import dotenv_values

MODEL_ID = "scribe_v3"
API_KEY_ENV = "ELEVENLABS_API_KEY"
CARTESIA_KEY_ENV = "CARTESIA_API_KEY"
DEEPGRAM_KEY_ENV = "DEEPGRAM_API_KEY"
MISSING_KEY = f"Set {DEEPGRAM_KEY_ENV}, {CARTESIA_KEY_ENV}, or {API_KEY_ENV} in the project .env file."
PROJECT_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


class TranscriptionError(RuntimeError):
    """A transcription failure that can be displayed to the user."""


def _credentials():
    values = dotenv_values(PROJECT_DIR / ".env", interpolate=False)
    deepgram = (os.environ.get(DEEPGRAM_KEY_ENV) or values.get(DEEPGRAM_KEY_ENV) or "").strip()
    if deepgram:
        return "Deepgram", deepgram
    cartesia = (os.environ.get(CARTESIA_KEY_ENV) or values.get(CARTESIA_KEY_ENV) or "").strip()
    if cartesia:
        return "Cartesia", cartesia
    elevenlabs = (os.environ.get(API_KEY_ENV) or values.get(API_KEY_ENV) or "").strip()
    return "ElevenLabs", elevenlabs


def provider_name() -> str:
    return _credentials()[0]


def has_api_key() -> bool:
    return bool(_credentials()[1])


def _post_with_system_trust(url, **kwargs):
    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.SSLError:
        if sys.platform != "win32" or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE"):
            raise
        # Windows may trust a root certificate that the bundled Requests file lacks.
        certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
        if not certificates:
            raise
        with TemporaryDirectory(prefix="ultron-stt-") as directory:
            bundle = Path(directory) / "trusted.pem"
            bundle.write_text("".join(ssl.DER_cert_to_PEM_cert(cert) for cert in certificates), encoding="ascii")
            return requests.post(url, verify=str(bundle), **kwargs)


def transcribe_pcm(audio: bytes, sample_rate: int, channels: int) -> str | None:
    if not audio:
        return None
    provider, api_key = _credentials()
    if not api_key:
        raise TranscriptionError(MISSING_KEY)

    wav = io.BytesIO()
    with wave.open(wav, "wb") as recording:
        recording.setparams((channels, 2, sample_rate, 0, "NONE", "not compressed"))
        recording.writeframes(audio)

    audio_file = {"file": ("recording.wav", wav.getvalue(), "audio/wav")}
    if provider == "Deepgram":
        url = "https://api.deepgram.com/v1/listen"
        headers = {"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"}
        upload = {"params": {"model": "nova-3", "smart_format": "true"}, "data": wav.getvalue()}
    elif provider == "Cartesia":
        url = "https://api.cartesia.ai/stt"
        headers = {"Authorization": f"Bearer {api_key}", "Cartesia-Version": "2026-08-14"}
        upload = {"files": audio_file, "data": {"model": "ink-whisper", "language": "en"}}
    else:
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {"xi-api-key": api_key}
        upload = {"files": audio_file, "data": {"model_id": MODEL_ID, "tag_audio_events": "false",
                                                "timestamps_granularity": "none"}}
    try:
        response = _post_with_system_trust(
            url,
            headers=headers,
            timeout=(5, 20),
            allow_redirects=False,
            **upload,
        )
    except requests.Timeout:
        raise TranscriptionError(f"{provider} transcription timed out. Try recording again.") from None
    except (requests.exceptions.SSLError, OSError):
        raise TranscriptionError(f"Could not verify the {provider} HTTPS certificate.") from None
    except requests.RequestException:
        raise TranscriptionError(f"Could not reach {provider}. Check your internet connection.") from None

    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if provider == "ElevenLabs" and isinstance(detail, dict):
        if detail.get("status") == "api_key_id_used_as_api_key":
            raise TranscriptionError("ElevenLabs received a key ID. Use the secret API key from the dashboard.")
        if detail.get("code") == "invalid_api_key":
            raise TranscriptionError("ElevenLabs rejected the API key. Check the secret and Speech to Text access.")

    if response.status_code in (401, 403):
        raise TranscriptionError(f"{provider} rejected the request. Check your API key and Speech to Text access.")
    if response.status_code == 402:
        raise TranscriptionError(f"Check available credits and billing in your {provider} account.")
    if response.status_code == 429:
        raise TranscriptionError(f"{provider} rate limit reached. Try again shortly.")
    if response.status_code != 200:
        raise TranscriptionError(f"{provider} transcription failed (HTTP {response.status_code}).")
    if provider == "Deepgram":
        try:
            text = payload["results"]["channels"][0]["alternatives"][0]["transcript"]
        except (TypeError, KeyError, IndexError):
            text = None
    else:
        text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise TranscriptionError(f"{provider} returned an invalid transcript response.")
    return text.strip() or None
