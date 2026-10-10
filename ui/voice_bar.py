"""Push-to-talk mic toggle: records on a worker QThread, emits `dictated(str)` / `failed(str)`."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from core import voice as voice_core

from . import widgets


class _Worker(QThread):
    ok = Signal(str)
    err = Signal(str)

    def __init__(self, rec: voice_core.VoiceRecorder, transcribe=None, base_url: str = "", api_key=None, model: str = "") -> None:
        super().__init__()
        self._rec = rec
        # Service path (desktop app: keys live in the service): transcribe(wav)->text.
        # Direct path (keys available): base_url/api_key/model via core.voice.transcribe.
        self._transcribe = transcribe
        self._base, self._key, self._model = base_url, api_key, model

    def run(self) -> None:  # never touches widgets: UI stays responsive
        try:
            wav = self._rec.capture()
            if self._transcribe is not None:
                self.ok.emit(self._transcribe(wav))
            else:
                self.ok.emit(voice_core.transcribe(self._base, self._key, wav, self._model))
        except Exception as e:  # honest error to the status label / integrator
            self.err.emit(str(e))


class VoiceBar(QWidget):
    """Mic toggle + status label. Integrator connects `dictated` to the composer; NOT wired into chat_view here."""

    dictated = Signal(str)
    failed = Signal(str)
    speak_toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._base, self._key, self._model = "", None, ""
        self._model_kind = ""
        self._transcribe = None
        self._busy = False
        self._rec: voice_core.VoiceRecorder | None = None
        self._worker: _Worker | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.mic = widgets.icon_button("mic", tip="Push to talk: record voice input", on=self.toggle)
        self.status = widgets.label("Voice input ready", muted=True)
        self.speak = QCheckBox("Read replies aloud")
        self.speak.setToolTip("Speak each finished Bot reply out loud")
        self.speak.toggled.connect(self.speak_toggled.emit)
        lay.addWidget(self.mic)
        lay.addWidget(self.status, 1)
        lay.addWidget(self.speak)

    def set_provider(self, base_url: str, api_key: str | None, model: str) -> None:
        self._base, self._key, self._model = base_url or "", api_key, model or ""

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.mic.setEnabled(not busy and self._worker is None)

    def toggle(self) -> None:
        if self._worker is not None:  # recording -> stop; worker finishes capture+transcribe
            if self._rec is not None:
                self._rec.stop()
            self.status.setText("Transcribing…")
            self.mic.setEnabled(False)
            return
        if self._busy:
            return
        if self._transcribe is None:
            try:
                stt_model = voice_core.pick_stt_model(self._model_kind, self._model) if self._model_kind else (self._model or "")
                if not stt_model:
                    raise ValueError("No STT model set.")
            except Exception as e:
                self.status.setText(str(e))
                self.failed.emit(str(e))
                return
            if not self._base or not stt_model:
                msg = "No speech endpoint set: pick a provider with STT support first."
                self.status.setText(msg)
                self.failed.emit(msg)
                return
        else:
            stt_model = ""
        try:
            self._rec = voice_core.VoiceRecorder()
        except Exception as e:
            msg = f"Microphone unavailable ({e})."
            self.status.setText(msg)
            self.failed.emit(msg)
            return
        self._worker = _Worker(self._rec, transcribe=self._transcribe, base_url=self._base, api_key=self._key, model=stt_model)
        self._worker.ok.connect(self._done)
        self._worker.err.connect(self._fail)
        self._worker.finished.connect(self._cleanup)
        self.status.setText("Listening… tap mic to stop")
        self.mic.setEnabled(True)
        self._worker.start()

    def set_transcriber(self, fn) -> None:
        """Service path: fn(wav_bytes)->text (keys stay in the service). Clears direct mode."""
        self._transcribe = fn

    def set_provider_kind(self, kind: str) -> None:
        """Optional: provider kind (openai/groq/…) so STT defaults apply when model is blank."""
        self._model_kind = kind

    def _done(self, text: str) -> None:
        self.status.setText("Voice input ready")
        self.dictated.emit(text)

    def _fail(self, msg: str) -> None:
        self.status.setText(msg[:160])
        self.failed.emit(msg)

    def _cleanup(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker, self._rec = None, None
        self.mic.setEnabled(not self._busy)
        if self.status.text() == "Transcribing…":
            self.status.setText("Voice input ready")
