"""Pure unit tests for core.voice: no network, no mic, no Qt."""
from __future__ import annotations

import io
import wave

import pytest

from core import voice


def test_pick_stt_defaults():
    assert voice.pick_stt_model("openai", "") == "whisper-1"
    assert voice.pick_stt_model("OpenAI", None) == "whisper-1"
    assert voice.pick_stt_model("groq", "") == "whisper-large-v3-turbo"


def test_pick_stt_explicit_wins():
    assert voice.pick_stt_model("openai", "whisper-large-v3") == "whisper-large-v3"
    assert voice.pick_stt_model("xai", "custom-stt") == "custom-stt"


def test_pick_stt_xai_raises_helpfully():
    with pytest.raises(ValueError, match="(?i)no.*STT|transcriptions"):
        voice.pick_stt_model("xai", "")
    with pytest.raises(ValueError):
        voice.pick_stt_model("other-thing", None)


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {"text": "hello there"}
        self.text = text or "body"

    def json(self):
        return self._payload


def test_transcribe_request_shape(monkeypatch):
    import httpx

    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = voice.transcribe("https://api.example.com/v1/", "sk-x", b"RIFF....", "whisper-1")
    assert out == "hello there"
    assert seen["url"] == "https://api.example.com/v1/audio/transcriptions"
    name, (fname, payload, mime) = next(iter(seen["files"].items()))
    assert name == "file" and fname == "audio.wav" and payload == b"RIFF...." and mime == "audio/wav"
    assert seen["data"] == {"model": "whisper-1"}
    assert seen["headers"] == {"Authorization": "Bearer sk-x"}
    assert seen["timeout"] == 60.0


def test_transcribe_no_key_sends_no_auth(monkeypatch):
    import httpx

    seen = {}

    def fake_post(url, **kw):
        seen.update(kw)
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    voice.transcribe("http://local:8080/v1", None, b"RIFF", "whisper-1")
    assert seen["headers"] == {}


def test_transcribe_honest_errors(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(401, text="bad key"))
    with pytest.raises(RuntimeError, match="(?i)auth|API key"):
        voice.transcribe("https://x/v1", "k", b"RIFF", "whisper-1")

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"text": "   "}))
    with pytest.raises(RuntimeError, match="(?i)no transcript"):
        voice.transcribe("https://x/v1", "k", b"RIFF", "whisper-1")

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(RuntimeError, match="(?i)failed|connect|endpoint"):
        voice.transcribe("https://x/v1", "k", b"RIFF", "whisper-1")

    with pytest.raises(ValueError):
        voice.transcribe("", "k", b"RIFF", "whisper-1")
    with pytest.raises(ValueError):
        voice.transcribe("https://x/v1", "k", b"", "whisper-1")


def test_speak_no_win32com_is_graceful_noop(monkeypatch, caplog):
    monkeypatch.setattr(voice, "_voice", None, raising=False)

    def no_sapi():
        raise ImportError("No module named 'win32com'")

    monkeypatch.setattr(voice, "_get_speaker", no_sapi)
    with caplog.at_level("WARNING"):
        assert voice.speak("hello") is False
    assert "win32com" in caplog.text or "TTS" in caplog.text
    voice.stop_speaking()  # must not raise


def test_speak_uses_purge_sync_flags(monkeypatch):
    calls = []

    class FakeVoice:
        def Speak(self, text, flags):
            calls.append((text, flags))

    monkeypatch.setattr(voice, "_get_speaker", lambda: FakeVoice())
    assert voice.speak("hi") is True
    assert calls == [("hi", 2)]
    voice.stop_speaking()
    assert calls[-1] == ("", 3)
    assert voice.speak("   ") is False  # blank text is a no-op


def test_build_wav_bytes_roundtrip():
    pcm = b"\x01\x00\xff\x7f" * 80  # 160 int16 frames
    raw = voice.build_wav_bytes(pcm, 16000)
    with wave.open(io.BytesIO(raw), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 160)
