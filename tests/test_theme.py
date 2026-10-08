"""Appearance options (accent, density, text size) and the command-palette matcher."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui import theme  # noqa: E402
from ui.main_window import _score  # noqa: E402

# qss() renders helper pixmaps (dropdown arrows, checkbox ticks) which require an application.
_app = QApplication.instance() or QApplication([])  # noqa: F841


def luma(c: str) -> float:
    q = QColor(c)
    return 0.299 * q.redF() + 0.587 * q.greenF() + 0.114 * q.blueF()


def _lin(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(c: str) -> float:
    """WCAG relative luminance (0..1)."""
    q = QColor(c)
    r, g, b = _lin(q.red()), _lin(q.green()), _lin(q.blue())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio (1..21)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class ContrastAA(unittest.TestCase):
    """WCAG AA (4.5:1) for text colours used in both themes — regressions fail loudly."""

    MIN = 4.5

    def _p(self, name: str) -> dict:
        theme.set_theme(name)
        return theme.palette()

    def tearDown(self):
        theme.configure("indigo", "comfortable", "default")
        theme.set_theme("dark")

    def test_text_on_surfaces(self):
        for name in ("dark", "light"):
            p = self._p(name)
            for surface in ("bg", "panel", "panel2"):
                for fg in ("text", "muted", "faint"):
                    self.assertGreaterEqual(contrast(p[fg], p[surface]), self.MIN, f"{fg} on {surface} ({name})")

    def test_semantic_colors_on_their_bubbles(self):
        for name in ("dark", "light"):
            p = self._p(name)
            for fg, bg in (("ok", "ok_bg"), ("warn", "warn_bg"), ("bad", "bad_bg")):
                self.assertGreaterEqual(contrast(p[fg], p[bg]), self.MIN, f"{fg} on {bg} ({name})")

    def test_button_text_on_accent(self):
        for name in ("dark", "light"):
            theme.set_theme(name)
            for key in theme.ACCENTS:
                theme.configure(accent=key)
                p = theme.palette()
                self.assertGreaterEqual(contrast(p["accent_text"], p["accent"]), self.MIN, f"{key} accent_text on accent ({name})")
                self.assertGreaterEqual(contrast(p["accent_text"], p["accent_hi"]), self.MIN, f"{key} accent_text on accent_hi ({name})")

    def test_work_chip_text_on_accent_dim(self):
        for name in ("dark", "light"):
            theme.set_theme(name)
            p = theme.palette()
            self.assertGreaterEqual(contrast(theme.chip_work_fg(), p["accent_dim"]), self.MIN, f"chip work fg on accent_dim ({name})")
            for key in theme.ACCENTS:
                theme.configure(accent=key)
                p = theme.palette()
                self.assertGreaterEqual(contrast(theme.chip_work_fg(), p["accent_dim"]), self.MIN, f"{key} chip work fg ({name})")

    def test_selected_row_text(self):
        for name in ("dark", "light"):
            p = self._p(name)
            self.assertGreaterEqual(contrast(p["text"], p["select"]), self.MIN, f"text on select ({name})")

    def test_assistive_luma_gap(self):
        for name in ("dark", "light"):
            p = self._p(name)
            self.assertGreater(abs(luma(p["accent"]) - luma(p["accent_text"])), 0.3)


class InteractionStates(unittest.TestCase):
    """The stylesheet must expose focus/pressed/checked affordances in both themes."""

    def tearDown(self):
        theme.set_theme("dark")

    def test_qss_has_focus_and_pressed_rules(self):
        for name in ("dark", "light"):
            theme.set_theme(name)
            s = theme.qss()
            self.assertIn(":focus", s, name)
            self.assertIn(":pressed", s, name)
            self.assertNotIn("focus-within", s, name)

    def test_qss_uses_radius_tokens(self):
        s = theme.qss()
        for key in ("card", "pill", "big"):
            self.assertIn(f"{theme.RADIUS[key]}px", s, key)

    def test_qss_styled_widgets_present(self):
        s = theme.qss()
        for needle in ("QTimeEdit", "QCheckBox::indicator:checked", "image: url(", "chiplike"):
            self.assertIn(needle, s)

    def test_qss_differs_between_themes(self):
        theme.set_theme("dark")
        dark = theme.qss()
        theme.set_theme("light")
        self.assertNotEqual(dark, theme.qss())


class ThemeOptions(unittest.TestCase):
    def tearDown(self):
        theme.configure("indigo", "comfortable", "default")
        theme.set_theme("dark")

    def test_every_accent_has_readable_button_text(self):
        for name in ("dark", "light"):
            theme.set_theme(name)
            for key in theme.ACCENTS:
                theme.configure(accent=key)
                p = theme.palette()
                self.assertGreater(abs(luma(p["accent"]) - luma(p["accent_text"])), 0.3, f"{key}/{name}: button text is hard to read")

    def test_accent_drives_derived_colours(self):
        theme.configure(accent="indigo")
        a = dict(theme.palette())
        theme.configure(accent="rose")
        b = theme.palette()
        for k in ("accent", "accent_hi", "accent_dim", "accent_soft", "select", "user"):
            self.assertNotEqual(a[k], b[k], k)
        self.assertEqual(a["bg"], b["bg"])           # surfaces do not change

    def test_unknown_values_are_ignored(self):
        theme.configure(accent="nope", density="nope", text="nope")
        self.assertEqual(theme.options(), {"accent": "indigo", "density": "comfortable", "text": "default"})
        theme.configure(None, None, None)
        self.assertEqual(theme.options()["accent"], "indigo")

    def test_density_and_text_size(self):
        theme.configure(density="compact", text="large")
        self.assertLess(theme.dp(10), 10)
        self.assertGreater(theme.base_size(), 13)
        self.assertIn("font-size: 15px", theme.qss())
        theme.configure(density="comfortable", text="small")
        self.assertEqual(theme.dp(10), 10)
        self.assertEqual(theme.base_size(), 12)

    def test_set_theme_keeps_options(self):
        theme.configure(accent="teal")
        theme.set_theme("light")
        self.assertEqual(theme.palette()["accent"], theme.ACCENTS["teal"][2])
        theme.set_theme("dark")
        self.assertEqual(theme.palette()["accent"], theme.ACCENTS["teal"][1])


class PaletteMatching(unittest.TestCase):
    def test_ranking(self):
        self.assertGreater(_score("inb", "Inbox"), _score("inb", "My inbox"))       # prefix beats word start
        self.assertGreater(_score("box", "Inbox"), _score("ibx", "Inbox"))          # substring beats loose match
        self.assertGreater(_score("ibx", "Inbox"), 0)                                # loose subsequence still matches
        self.assertEqual(_score("zzz", "Inbox"), 0)
        self.assertGreater(_score("set", "Settings"), _score("set", "Reset the Bot"))


if __name__ == "__main__":
    unittest.main()
