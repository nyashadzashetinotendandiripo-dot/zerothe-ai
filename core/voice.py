"""Push-to-talk voice input + spoken replies (Qt-free; safe to use from worker threads)."""
from __future__ import annotations

import io
import logging
import threading
import wave
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1

_STT_DEFAULTS = {"openai": "whisper-1", "groq": "whisper-large-v3-turbo"}
_NO_STT_HINT = (
    "Provider '{kind}' has no Whisper-compatible STT endpoint configured. "
    "Point the provider at an OpenAI-compatible /audio/transcriptions endpoint "
    "(OpenAI, Groq or a local server) or pick a provider with one."
)


def pick_stt_model(provider_kind: str, configured_model: str | None) -> str:
    """Return the STT model to send. An explicit configured model always wins."""
    if configured_model and configured_model.strip():
        return configured_model.strip()
    key = (provider_kind or "").strip().lower()
    if key in _STT_DEFAULTS:
        return _STT_DEFAULTS[key]
    raise ValueError(_NO_STT_HINT.format(kind=provider_kind or "unknown"))


def transcribe(base_url: str, api_key: str | None, wav_bytes: bytes, model: str) -> str:
    """POST wav_bytes to {base_url}/audio/transcriptions, return the transcript text."""
    import httpx

    if not base_url or not base_url.strip():
        raise ValueError("No STT base_url set. Pick a provider with an STT endpoint first.")
    if not wav_bytes:
        raise ValueError("Nothing recorded: wav_bytes is empty.")
    if not model or not model.strip():
        raise ValueError("No STT model set.")
    url = base_url.rstrip("/") + "/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.post(url, files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                           data={"model": model.strip()}, headers=headers, timeout=60.0)
    except Exception as e:  # httpx.TimeoutException / ConnectError etc.
        raise RuntimeError(f"STT request to {url} failed ({type(e).__name__}: {e}). "
                           "Check the endpoint is reachable and try again.") from e
    if resp.status_code in (401, 403):
        raise RuntimeError(f"STT authentication failed (HTTP {resp.status_code}). Check the API key in Settings > Providers.")
    if resp.status_code == 429:
        raise RuntimeError("STT rate limited (HTTP 429). Wait a moment and try again.")
    if resp.status_code >= 400:
        raise RuntimeError(f"STT request failed (HTTP {resp.status_code}): {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"STT endpoint returned non-JSON: {resp.text[:200]!r}") from e
    text = str(data.get("text", "") or "").strip()
    if not text:
        raise RuntimeError("STT endpoint returned no transcript text.")
    return text


def build_wav_bytes(pcm_s16le: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw int16 mono PCM bytes in a WAV container (16kHz mono by default)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_s16le)
    return buf.getvalue()


class VoiceRecorder:
    """16kHz mono mic capture with a stop flag. Blocking `capture()` returns WAV bytes."""

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def capture(self, max_seconds: float = 120.0) -> bytes:
        try:
            import sounddevice as sd  # type: ignore
        except ImportError as e:
            raise RuntimeError("Mic capture needs the 'sounddevice' package: pip install sounddevice numpy.") from e
        import numpy as np  # type: ignore

        frames: list[bytes] = []
        done = threading.Event()

        def cb(indata: Any, nframes: int, _time: Any, status: Any) -> None:
            if status:
                log.debug("sounddevice status: %s", status)
            frames.append(np.ascontiguousarray(indata, dtype=np.int16).tobytes())
            if self._stop.is_set():
                done.set()

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=CHANNELS, dtype="int16", callback=cb):
                done.wait(timeout=max_seconds)
        except Exception as e:
            raise RuntimeError(f"Mic capture failed ({type(e).__name__}: {e}). "
                               "Check a microphone is connected and not used by another app.") from e
        if not frames:
            raise RuntimeError("Nothing recorded: no audio frames captured.")
        return build_wav_bytes(b"".join(frames), self.sample_rate)


_voice: Any = None


def _get_speaker() -> Any:
    global _voice
    if _voice is not None:
        return _voice
    import win32com.client  # type: ignore

    _voice = win32com.client.Dispatch("SAPI.SpVoice")
    return _voice


def speak(text: str) -> bool:
    """Speak text synchronously via Windows SAPI (purges queued speech first). False when TTS unavailable."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        _get_speaker().Speak(text, 2)  # 2 = SVSFPurgeBeforeSpeak, synchronous
    except ImportError:
        log.warning("TTS unavailable: pywin32 (win32com) is not installed; skipping spoken reply.")
        return False
    except Exception as e:
        log.warning("TTS speak failed (%s: %s); skipping spoken reply.", type(e).__name__, e)
        return False
    return True


def stop_speaking() -> None:
    """Purge any queued/in-progress speech. No-op when SAPI is unavailable."""
    try:
        _get_speaker().Speak("", 3)  # async + purge = stop now
    except ImportError:
        log.info("TTS stop ignored: pywin32 (win32com) is not installed.")
    except Exception as e:
        log.warning("TTS stop failed (%s: %s).", type(e).__name__, e)
