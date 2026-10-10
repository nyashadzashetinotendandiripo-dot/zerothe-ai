"""Offscreen-safe tests for ui.screen_share.

Run: QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_screen_share.py -q
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui import screen_share  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_capture_window_never_raises() -> None:
    pm = screen_share.capture_window()
    assert pm is None or isinstance(pm, QPixmap)
    if pm is not None:
        assert not pm.isNull()


def test_pixmap_to_png_bytes_png_magic() -> None:
    pm = QPixmap(100, 100)
    pm.fill(QColor("red"))
    data = screen_share.pixmap_to_png_bytes(pm)
    assert isinstance(data, bytes)
    assert data.startswith(_PNG_MAGIC)


def test_pixmap_to_png_bytes_bad_input() -> None:
    assert screen_share.pixmap_to_png_bytes(None) == b""
    assert screen_share.pixmap_to_png_bytes(QPixmap()) == b""


def test_fallback_path_when_printwindow_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sentinel = QPixmap(10, 10)
    sentinel.fill(QColor("blue"))

    monkeypatch.setattr(screen_share, "_capture_foreground_win32",
                        lambda: (_ for _ in ()).throw(RuntimeError("PrintWindow exploded")))
    monkeypatch.setattr(screen_share, "_capture_fullscreen", lambda: sentinel)
    assert screen_share.capture_window() is sentinel
