"""Screen context: capture the frontmost window (or full screen) as a QPixmap.

The integrator wires hotkeys and the chat composer; this module only captures
(Qt + ctypes, no other deps) and never raises from its public entry points.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

_PW_RENDERFULLCONTENT = 0x00000002
_MAX_DIM = 16384  # sanity cap against bogus rects


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("header", _BitmapInfoHeader), ("colors", ctypes.c_uint32 * 3)]


def _capture_foreground_win32() -> QPixmap | None:
    """Capture the frontmost window via Win32 PrintWindow. None on any failure."""
    if sys.platform != "win32":
        return None
    try:
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        user32, gdi32 = windll.user32, windll.gdi32
        from ctypes import wintypes
    except Exception:
        return None
    hwnd = None
    hdc_screen = hdc_mem = hbm = None
    old_obj = None
    try:
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd):
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if not (0 < w <= _MAX_DIM and 0 < h <= _MAX_DIM):
            return None
        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            return None
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        if not hdc_mem or not hbm:
            return None
        old_obj = gdi32.SelectObject(hdc_mem, hbm)
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        if not user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT):
            if not user32.PrintWindow(hwnd, hdc_mem, 0):  # legacy fallback flag
                return None
        bmi = _BitmapInfo()
        bmi.header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bmi.header.biWidth = w
        bmi.header.biHeight = -h  # top-down DIB (else rows come out flipped)
        bmi.header.biPlanes = 1
        bmi.header.biBitCount = 32
        buf = (ctypes.c_uint8 * (w * h * 4))()
        if not gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0):
            return None
        img = QImage(bytes(buf), w, h, w * 4, QImage.Format.Format_RGB32).copy()
        if img.isNull():
            return None
        pm = QPixmap.fromImage(img.rgbSwapped())
        return pm if not pm.isNull() else None
    except Exception:
        return None
    finally:
        try:
            if hdc_mem and old_obj:
                gdi32.SelectObject(hdc_mem, old_obj)
            if hbm:
                gdi32.DeleteObject(hbm)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            if hdc_screen:
                user32.ReleaseDC(None, hdc_screen)
        except Exception:
            pass


def _capture_fullscreen() -> QPixmap | None:
    """Grab the primary screen. None when headless/unsupported."""
    app = QApplication.instance() or QApplication([])
    screen = QApplication.primaryScreen()
    if screen is None:
        return None
    pm = screen.grabWindow(0)
    return pm if not pm.isNull() else None


def capture_window() -> QPixmap | None:
    """Frontmost window via PrintWindow, else full screen. Never raises."""
    try:
        pm = _capture_foreground_win32()
        if pm is not None and not pm.isNull():
            return pm
    except Exception:
        pass
    try:
        return _capture_fullscreen()
    except Exception:
        return None


def pixmap_to_png_bytes(pm: QPixmap | None) -> bytes:
    """Encode a pixmap as PNG bytes via QBuffer. b"" on bad input. Never raises."""
    try:
        if pm is None or pm.isNull():
            return b""
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        try:
            if not pm.save(buf, "PNG"):
                return b""
            return bytes(buf.data())
        finally:
            buf.close()
    except Exception:
        return b""
