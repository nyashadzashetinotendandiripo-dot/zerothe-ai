"""Design system: calm dark theme (default) and a light variant.

Principles: one accent colour, quiet surfaces separated by hairlines instead of boxes, generous spacing on a 4px grid,
no motion or colour flashes while a Bot works. Status is shown with small dots and soft tinted pills.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF

DARK = {
    "bg": "#0e1014", "panel": "#13161c", "panel2": "#1a1e26", "raised": "#20252f", "line": "#232832", "line2": "#2d3340",
    "text": "#e8eaf0", "muted": "#8a92a6", "faint": "#7d8598", "accent": "#7c9cff", "accent_hi": "#98b2ff", "accent_text": "#0b0e16",
    "accent_dim": "#232f55", "accent_soft": "#1a2240", "ok": "#5bc48a", "ok_bg": "#15291f", "warn": "#e3b45f", "warn_bg": "#2e2514",
    "bad": "#e47c7c", "bad_bg": "#2e1a1d", "user": "#222d52", "code": "#0b0d12", "hover": "#191d25", "select": "#1b2236",
}
LIGHT = {
    "bg": "#f5f6f9", "panel": "#ffffff", "panel2": "#f0f2f7", "raised": "#e8ebf2", "line": "#e3e6ee", "line2": "#d3d8e3",
    "text": "#1b2030", "muted": "#565f72", "faint": "#626a7d", "accent": "#3d63dd", "accent_hi": "#5476e8", "accent_text": "#ffffff",
    "accent_dim": "#dbe4ff", "accent_soft": "#eaf0ff", "ok": "#1a7749", "ok_bg": "#e3f4eb", "warn": "#906010", "warn_bg": "#fbf0d9",
    "bad": "#b03a3a", "bad_bg": "#fbe6e6", "user": "#e1e9ff", "code": "#eef0f6", "hover": "#eceff5", "select": "#e6ecff",
}
# Corner-radius tokens (the 4px-grid radius scale; QSS needs literals so these are inlined into qss()).
RADIUS = {"xs": 4, "sm": 6, "md": 8, "lg": 9, "xl": 10, "card": 14, "card_sm": 12, "big": 18, "pill": 14}
_current = dict(DARK)
_name = "dark"        # the theme in use: dark or light
_pref = "dark"        # what the user chose: dark, light or auto (match Windows)

FONT = '"Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", Arial, sans-serif'

# Appearance options (stored per user in the UI config, not in the service): accent colour, density and text size.
ACCENTS = {   # key: (label, dark-theme colour, light-theme colour)
    "indigo": ("Indigo", "#7c9cff", "#3d63dd"),
    "violet": ("Violet", "#a58bff", "#6d4fe0"),
    "teal": ("Teal", "#47c7bd", "#0f8f89"),
    "green": ("Green", "#5bc48a", "#2a9460"),
    "amber": ("Amber", "#f0b45a", "#b36a00"),
    "rose": ("Rose", "#f27a9d", "#d03c6c"),
}
DENSITIES = {"comfortable": ("Comfortable", 1.0), "compact": ("Compact", 0.78)}
TEXT_SIZES = {"small": ("Small", 12), "default": ("Default", 13), "large": ("Large", 15)}
_opts = {"accent": "indigo", "density": "comfortable", "text": "default"}


def _mix(a: str, b: str, t: float) -> str:
    """Blend colour a towards b by t (0..1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def _luma(c: str) -> float:
    q = QColor(c)
    return 0.299 * q.redF() + 0.587 * q.greenF() + 0.114 * q.blueF()


def _contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1..21)."""
    def lum(c: str) -> float:
        def f(v: float) -> float:
            v /= 255.0
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        q = QColor(c)
        return 0.2126 * f(q.red()) + 0.7152 * f(q.green()) + 0.0722 * f(q.blue())
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _rebuild() -> None:
    base = LIGHT if _name == "light" else DARK
    _current.clear()
    _current.update(base)
    label, dark_c, light_c = ACCENTS[_opts["accent"]]
    acc = light_c if _name == "light" else dark_c
    bg, panel = base["bg"], base["panel"]
    # Hover must move the accent AWAY from its label colour so text contrast only improves:
    # white labels get a darker fill, near-black labels a lighter one (dark theme is always black-on-bright).
    text = "#ffffff" if _contrast(acc, "#ffffff") >= _contrast(acc, "#000000") else "#000000"
    if text == "#ffffff":
        hi = _mix(acc, "#000000", 0.18 if _name == "dark" else 0.12)
    else:
        hi = _mix(acc, "#ffffff", 0.18 if _name == "dark" else 0.10)
    _current.update(
        accent=acc, accent_hi=hi, accent_text=text,
        accent_dim=_mix(panel, acc, 0.30 if _name == "dark" else 0.22), accent_soft=_mix(panel, acc, 0.14 if _name == "dark" else 0.09),
        select=_mix(panel, acc, 0.16 if _name == "dark" else 0.11), user=_mix(bg, acc, 0.26 if _name == "dark" else 0.16))


def palette() -> dict:
    return _current


def theme_name() -> str:
    return _name


def _apps_use_light() -> bool | None:
    """Windows' own light/dark setting for apps (None when it cannot be read, e.g. on other systems)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except (ImportError, OSError):
        return None


def system_theme() -> str:
    return "light" if _apps_use_light() else "dark"


def preference() -> str:
    return _pref


def set_theme(name: str) -> dict:
    """name: dark | light | auto. `auto` follows Windows' app mode."""
    global _name, _pref
    _pref = name if name in ("light", "dark", "auto") else "dark"
    _name = system_theme() if _pref == "auto" else _pref
    _rebuild()
    return _current


def configure(accent: str | None = None, density: str | None = None, text: str | None = None) -> None:
    """Set appearance options (unknown values are ignored) and recompute the palette."""
    if accent in ACCENTS:
        _opts["accent"] = accent
    if density in DENSITIES:
        _opts["density"] = density
    if text in TEXT_SIZES:
        _opts["text"] = text
    _rebuild()


def options() -> dict:
    return dict(_opts)


def dp(n: float) -> int:
    """Scale a spacing value by the density setting (compact = tighter)."""
    return max(1, round(n * DENSITIES[_opts["density"]][1]))


def base_size() -> int:
    return TEXT_SIZES[_opts["text"]][1]


def _arrow_url(color: str) -> str:
    """Render a small chevron to a temp PNG so combo boxes show a dropdown arrow (QSS needs a file)."""
    try:
        import os
        import tempfile

        from . import icons
        d = os.path.join(tempfile.gettempdir(), "opengrokbot-ui")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"chevron-{color.strip('#')}.png")
        if not os.path.exists(path):
            icons.pixmap("chevron-down", color, 12, 2.2).save(path, "PNG")
        return path.replace("\\", "/")
    except Exception:
        return ""


def chip_work_fg() -> str:
    """Foreground for the 'working' status chip: must stay WCAG AA on the accent-tinted chip background."""
    acc = _current["accent"]
    return _mix(acc, "#ffffff", 0.22) if _name == "dark" else _mix(acc, "#000000", 0.30)


def _check_url(color: str) -> str:
    """Render a white checkmark to a temp PNG so checked checkboxes show a tick (QSS needs a file)."""
    try:
        import os
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "opengrokbot-ui")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"check-{color.strip('#')}.png")
        if not os.path.exists(path):
            pm = QPixmap(16, 16)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(color), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(QPolygonF([QPointF(3.5, 8.5), QPointF(6.5, 11.5), QPointF(12.5, 5.0)]))
            p.end()
            pm.save(path, "PNG")
        return path.replace("\\", "/")
    except Exception:
        return ""


def qss() -> str:
    c = _current
    arrow = _arrow_url(c["muted"])
    check = _check_url(c["accent_text"])
    work_fg = chip_work_fg()
    r = RADIUS
    acc_press = _mix(c["accent"], "#000000", 0.20)
    if _contrast(c["accent_text"], acc_press) < 4.5:   # pressed fill must not eat label contrast
        acc_press = _mix(c["accent"], "#ffffff", 0.18)
    fs, small, tiny = base_size(), base_size() - 1, base_size() - 2
    bv, bh = dp(6), dp(14) if dp(14) > 9 else 9
    iv = dp(8)
    arrow_rule = f"QComboBox::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}" if arrow else ""
    return f"""
* {{ font-family: {FONT}; font-size: {fs}px; color: {c['text']}; outline: none; }}
QWidget {{ background: transparent; }}
QMainWindow, QDialog, QMessageBox, QInputDialog, QWidget#root {{ background: {c['bg']}; }}
QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line2']}; padding: 5px 8px; border-radius: 6px; }}

QLabel[muted="true"] {{ color: {c['muted']}; }}
QLabel[faint="true"] {{ color: {c['faint']}; font-size: {small}px; }}
QLabel[h1="true"] {{ font-size: {fs + 9}px; font-weight: 600; letter-spacing: -0.2px; }}
QLabel[h2="true"] {{ font-size: {fs + 1}px; font-weight: 600; }}
QLabel[eyebrow="true"] {{ color: {c['faint']}; font-size: {tiny}px; font-weight: 600; letter-spacing: 1px; }}
QLabel[chip="true"] {{ background: {c['panel2']}; color: {c['muted']}; border-radius: {r['lg']}px; padding: 2px 10px; font-size: {tiny}px; }}
QLabel[chip="ok"] {{ background: {c['ok_bg']}; color: {c['ok']}; border-radius: {r['lg']}px; padding: 2px 10px; font-size: {tiny}px; }}
QLabel[chip="warn"] {{ background: {c['warn_bg']}; color: {c['warn']}; border-radius: {r['lg']}px; padding: 2px 10px; font-size: {tiny}px; }}
QLabel[chip="bad"] {{ background: {c['bad_bg']}; color: {c['bad']}; border-radius: {r['lg']}px; padding: 2px 10px; font-size: {tiny}px; }}
QLabel[chip="work"] {{ background: {c['accent_dim']}; color: {work_fg}; border-radius: {r['lg']}px; padding: 2px 10px; font-size: {tiny}px; font-weight: 600; }}
QLabel[cite="true"] {{ background: {c['panel2']}; border: 1px solid {c['line2']}; border-radius: {r['pill']}px; padding: 3px 10px; font-size: {tiny}px; }}
QLabel[cite="true"]:hover {{ border-color: {c['accent']}; }}
QLabel[badge="true"] {{ background: {c['accent']}; color: {c['accent_text']}; border-radius: {r['lg']}px; padding: 0px 6px; font-size: {tiny}px; font-weight: 600; }}

QFrame[card="true"] {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: {r['card']}px; }}
QFrame[card="hover"] {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: {r['card_sm']}px; }}
QFrame[card="hover"]:hover {{ border: 1px solid {c['accent_dim']}; background: {c['hover']}; }}
QFrame[card="approval"] {{ background: {c['warn_bg']}; border: 1px solid {c['line2']}; border-left: 3px solid {c['warn']}; border-radius: {r['card_sm']}px; }}
QFrame[card="question"] {{ background: {c['accent_soft']}; border: 1px solid {c['line2']}; border-left: 3px solid {c['accent']}; border-radius: {r['card_sm']}px; }}
QFrame[card="tile"] {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: {r['card']}px; }}
QFrame[card="tile"][hot="true"] {{ background: {c['warn_bg']}; border: 1px solid {c['line2']}; }}
QFrame[card="plain"] {{ background: transparent; border: none; }}
QFrame[sep="true"] {{ background: {c['line']}; max-height: 1px; min-height: 1px; border: none; }}
QFrame[vsep="true"] {{ background: {c['line']}; max-width: 1px; min-width: 1px; border: none; }}
QFrame#sidebar {{ background: {c['panel']}; border-right: 1px solid {c['line']}; }}
QFrame#composer {{ background: {c['panel']}; border: 1px solid {c['line2']}; border-radius: {r['big']}px; }}
QFrame#activity {{ background: {c['panel']}; border-left: 1px solid {c['line']}; }}
QFrame#toolgroup {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: {r['xl']}px; }}

QPushButton[chiplike="true"][flat="true"] {{ background: transparent; border: 1px solid {c['line2']}; border-radius: {r['pill']}px; padding: 5px 12px; color: {c['muted']}; }}
QPushButton[chiplike="true"][flat="true"]:hover {{ border-color: {c['accent']}; color: {c['text']}; background: {c['accent_soft']}; }}
QPushButton[chiplike="true"][flat="true"]:pressed {{ background: {c['raised']}; }}
QPushButton[chiplike="true"][primary="true"] {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']}; border-radius: {r['pill']}px; padding: 5px 12px; font-weight: 600; }}
QPushButton[chiplike="true"][primary="true"]:hover {{ background: {c['accent_hi']}; border-color: {c['accent_hi']}; }}
QPushButton[chiplike="true"][primary="true"]:pressed {{ background: {acc_press}; border-color: {acc_press}; }}
QPushButton[chiplike="true"]:focus {{ border-color: {c['accent']}; }}

QPushButton {{ background: {c['panel2']}; border: 1px solid {c['line2']}; border-radius: {r['lg']}px; padding: {bv}px {bh}px; min-height: 18px; }}
QPushButton:hover {{ background: {c['raised']}; }}
QPushButton:pressed {{ background: {c['line2']}; }}
QPushButton:focus {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['panel']}; border-color: {c['line']}; }}
QPushButton[primary="true"] {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {c['accent_hi']}; border-color: {c['accent_hi']}; }}
QPushButton[primary="true"]:pressed {{ background: {acc_press}; border-color: {acc_press}; }}
QPushButton[primary="true"]:focus {{ border-color: {c['accent_text']}; }}
QPushButton[primary="true"]:disabled {{ background: {c['accent_dim']}; color: {c['muted']}; border-color: {c['accent_dim']}; }}
QPushButton[danger="true"] {{ background: transparent; border: 1px solid {c['line2']}; color: {c['bad']}; }}
QPushButton[danger="true"]:hover {{ background: {c['bad_bg']}; border-color: {c['bad']}; }}
QPushButton[danger="true"]:pressed {{ background: {c['bad']}; color: #ffffff; border-color: {c['bad']}; }}
QPushButton[danger="true"]:focus {{ border-color: {c['text']}; }}
QPushButton[flat="true"] {{ background: transparent; border: 1px solid transparent; border-radius: {r['lg']}px; color: {c['muted']}; padding: 4px 8px; }}
QPushButton[flat="true"]:hover {{ color: {c['text']}; background: {c['hover']}; }}
QPushButton[flat="true"]:pressed {{ background: {c['line2']}; color: {c['text']}; }}
QPushButton[flat="true"]:focus {{ border-color: {c['accent']}; }}
QPushButton[iconbtn="true"] {{ background: transparent; border: 1px solid transparent; border-radius: {r['md']}px; padding: 0px; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; }}
QPushButton[iconbtn="true"]:hover {{ background: {c['hover']}; }}
QPushButton[iconbtn="true"]:pressed {{ background: {c['line2']}; }}
QPushButton[iconbtn="true"]:focus {{ border-color: {c['accent']}; }}
QPushButton[iconbtn="accent"] {{ background: {c['accent']}; border: 1px solid transparent; border-radius: 16px; padding: 0px; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; }}
QPushButton[iconbtn="accent"]:hover {{ background: {c['accent_hi']}; }}
QPushButton[iconbtn="accent"]:pressed {{ background: {acc_press}; }}
QPushButton[iconbtn="accent"]:focus {{ border-color: {c['text']}; }}
QPushButton[iconbtn="accent"]:disabled {{ background: {c['accent_dim']}; }}
QPushButton[iconbtn="true"][small="true"] {{ min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; border-radius: 6px; }}
QPushButton[iconbtn="danger"] {{ background: {c['bad_bg']}; border: 1px solid {c['bad']}; border-radius: 16px; padding: 0px; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; }}
QPushButton[iconbtn="danger"]:pressed {{ background: {c['bad']}; }}
QPushButton[iconbtn="danger"]:focus {{ border-color: {c['text']}; }}
QPushButton[side="true"] {{ background: transparent; border: 1px solid transparent; border-radius: {r['md']}px; text-align: left; padding: 6px 9px; color: {c['muted']}; }}
QPushButton[side="true"]:hover {{ background: {c['hover']}; color: {c['text']}; }}
QPushButton[side="true"]:pressed {{ background: {c['line2']}; }}
QPushButton[side="true"]:focus {{ border-color: {c['accent']}; }}
QPushButton[side="true"]:checked {{ background: {c['select']}; color: {c['text']}; }}
QPushButton[chipbtn="true"] {{ background: {c['panel']}; border: 1px solid {c['line2']}; border-radius: {r['pill']}px; padding: 6px 14px; color: {c['muted']}; }}
QPushButton[chipbtn="true"]:hover {{ border-color: {c['accent']}; color: {c['text']}; background: {c['accent_soft']}; }}
QPushButton[chipbtn="true"]:pressed {{ background: {c['raised']}; }}
QPushButton[chipbtn="true"]:focus {{ border-color: {c['text']}; background: {c['raised']}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{
  background: {c['panel']}; border: 1px solid {c['line2']}; border-radius: {r['md']}px; padding: 6px 10px; selection-background-color: {c['accent_dim']}; }}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover, QTimeEdit:hover {{ border-color: {c['faint']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit[bare="true"], QPlainTextEdit[bare="true"] {{ border: none; background: transparent; padding: 2px 4px; }}
QLineEdit[search="true"] {{ border-radius: {r['pill']}px; padding: 6px 12px 6px 12px; background: {c['panel2']}; border: 1px solid transparent; }}
QLineEdit[search="true"]:focus {{ border: 1px solid {c['accent']}; }}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
{arrow_rule}
QComboBox QAbstractItemView {{ background: {c['panel2']}; border: 1px solid {c['line2']}; border-radius: {r['md']}px; selection-background-color: {c['accent_dim']}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{ width: 0; border: none; }}
QCheckBox {{ spacing: 9px; }}
QCheckBox:focus::indicator {{ border-color: {c['accent']}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: {r['xs'] + 1}px; border: 1px solid {c['line2']}; background: {c['panel']}; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}
QCheckBox::indicator:disabled {{ background: {c['panel2']}; border-color: {c['line']}; image: none; }}
QCheckBox:disabled {{ color: {c['faint']}; }}

QListWidget, QTreeWidget, QTableWidget, QListView {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: {r['card']}px; }}
QListWidget::item {{ padding: {iv}px 10px; border-radius: {r['md']}px; margin: 1px 4px; }}
QListWidget::item:focus {{ background: {c['accent_soft']}; color: {c['text']}; }}
QListWidget::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QListWidget::item:hover:!selected {{ background: {c['hover']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {c['faint']}; border: none; border-bottom: 1px solid {c['line']}; padding: 9px 10px; font-size: {tiny}px; font-weight: 600; text-transform: uppercase; }}
QTableWidget {{ gridline-color: transparent; }}
QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid {c['line']}; }}
QTableWidget::item:focus {{ background: {c['accent_soft']}; color: {c['text']}; outline: 1px solid {c['accent']}; }}
QTableWidget::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QTableWidget::item:hover {{ background: {c['hover']}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

QTabWidget::pane {{ border: none; top: 0px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; padding: 9px 14px; margin-right: 2px; color: {c['muted']}; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar::tab:focus {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar::tab:hover:!selected {{ color: {c['text']}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['line2']}; border-radius: 4px; min-height: 36px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['line2']}; border-radius: 4px; min-width: 36px; margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0; background: transparent; }}
QSplitter::handle {{ background: {c['line']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QProgressBar {{ background: {c['panel2']}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QMenu {{ background: {c['raised']}; border: 1px solid {c['line2']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['accent_dim']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 5px 8px; }}
QTextBrowser {{ background: transparent; border: none; }}
QGroupBox {{ border: 1px solid {c['line']}; border-radius: 12px; margin-top: 16px; padding: 16px 14px 12px 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {c['muted']}; font-weight: 600; }}
QMessageBox, QInputDialog {{ background: {c['bg']}; }}
QDialogButtonBox QPushButton:default {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']}; font-weight: 600; }}
QDialogButtonBox QPushButton:default:hover {{ background: {c['accent_hi']}; }}
QDialogButtonBox QPushButton:default:pressed {{ background: {acc_press}; border-color: {acc_press}; }}
QDialogButtonBox QPushButton:default:focus {{ border-color: {c['accent_text']}; }}
QFrame[siderow="true"] {{ background: transparent; border: 1px solid transparent; border-radius: {r['lg']}px; }}
QFrame[siderow="true"]:hover {{ background: {c['hover']}; }}
QFrame[siderow="true"][focused="true"] {{ border: 1px solid {c['accent']}; }}
QFrame[siderow="true"][checked="true"] {{ background: {c['select']}; }}
QFrame[siderow="true"][checked="true"][focused="true"] {{ border: 1px solid {c['accent']}; }}
QFrame[swatch="true"] {{ border-radius: 13px; border: 2px solid transparent; }}
QFrame[swatch="true"][on="true"] {{ border: 2px solid {c['text']}; }}
QLabel[kbd="true"] {{ background: {c['panel2']}; color: {c['muted']}; border: 1px solid {c['line2']}; border-radius: {r['xs'] + 1}px; padding: 1px 6px; font-size: {tiny}px; }}
"""


def mono() -> QFont:
    f = QFont("Cascadia Mono, Consolas, DejaVu Sans Mono, monospace")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(max(8, round(base_size() * 0.75)))
    return f


def app_icon(size: int = 256) -> QIcon:
    """Draw the app icon (a friendly bot head) so no binary asset is required at runtime."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 128.0
    p.setBrush(QBrush(QColor("#12151c")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), 28 * s, 28 * s)
    p.setBrush(QBrush(QColor("#7c9cff")))
    p.drawRoundedRect(QRectF(26 * s, 34 * s, 76 * s, 62 * s), 18 * s, 18 * s)
    p.drawRoundedRect(QRectF(60 * s, 18 * s, 8 * s, 18 * s), 4 * s, 4 * s)
    p.setBrush(QBrush(QColor("#0f1115")))
    p.drawEllipse(QRectF(42 * s, 55 * s, 16 * s, 16 * s))
    p.drawEllipse(QRectF(70 * s, 55 * s, 16 * s, 16 * s))
    p.drawRoundedRect(QRectF(56 * s, 76 * s, 16 * s, 5 * s), 2.5 * s, 2.5 * s)
    p.setBrush(QBrush(QColor("#e6e8ee")))
    p.drawEllipse(QRectF(57 * s, 9 * s, 14 * s, 14 * s))
    p.end()
    return QIcon(pm)


def status_dot(color: str, size: int = 10) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(QPen(Qt.PenStyle.NoPen))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)
