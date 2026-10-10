"""Native widgets for Intelligent-UI reply blocks (protocol in core/uiblocks.py).

The model only composes these seven components; block actions can only send a
chat reply or open a link. Anything consequential still goes through tools and
the normal approval cards.
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSizePolicy, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import theme
from .widgets import button, card, label


@dataclass
class BlockCtx:
    reply: Callable[[str], None]
    open_url: Callable[[str], None]


def _wrap(children: list[QWidget], spacing: int = 8) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(spacing)
    for c in children:
        v.addWidget(c)
    return w


def _buttons_block(d: dict, ctx: BlockCtx) -> QWidget:
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    for i, it in enumerate((d.get("items") or [])[:6]):
        if not isinstance(it, dict) or not it.get("label"):
            continue
        text = str(it.get("reply") or it.get("label"))
        url = it.get("open") if isinstance(it.get("open"), str) else ""
        if url:
            b = button(str(it["label"]), on=lambda u=url: ctx.open_url(u))
        else:
            b = button(str(it["label"]), primary=(i == 0), on=lambda t=text: ctx.reply(t))
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        v.addWidget(b)
    return box


def _form_block(d: dict, ctx: BlockCtx) -> QWidget:
    out: list[QWidget] = []
    if d.get("title"):
        out.append(label(str(d["title"]), h2=True))
    fields: list[tuple[str, str, QWidget, Callable[[], object]]] = []
    for f in (d.get("fields") or [])[:8]:
        if not isinstance(f, dict) or not f.get("key") or not f.get("label"):
            continue
        key, kind = str(f["key"]), f.get("kind", "text")
        out.append(label(str(f["label"]), muted=True, wrap=False))
        if kind == "long":
            ed = QPlainTextEdit()
            ed.setPlaceholderText(str(f.get("placeholder") or ""))
            ed.setMaximumHeight(80)
            fields.append((key, "long", ed, ed.toPlainText))
        elif kind == "choice":
            ed = QComboBox()
            for o in (f.get("options") or [])[:12]:
                ed.addItem(str(o))
            fields.append((key, "choice", ed, ed.currentText))
        elif kind == "toggle":
            ed = QCheckBox(str(f["label"]))
            fields.append((key, "toggle", ed, ed.isChecked))
            out.pop()   # the checkbox carries its own label
        else:
            ed = QLineEdit()
            ed.setPlaceholderText(str(f.get("placeholder") or ""))
            fields.append((key, "text", ed, ed.text))
        out.append(ed)  # type: ignore[arg-type]
    sub = d.get("submit") if isinstance(d.get("submit"), dict) else {}
    tmpl = str(sub.get("message") or "")
    if not tmpl:
        tmpl = "\n".join("%s: {%s}" % (k, k) for k, _, _, _ in fields)

    def send() -> None:
        vals = {k: (("yes" if v() else "no") if kind == "toggle" else str(v())) for k, kind, _, v in fields}
        try:
            msg = tmpl.format(**vals)
        except (KeyError, IndexError, ValueError):
            msg = tmpl
            for k, val in vals.items():
                msg = msg.replace("{" + k + "}", val)
        if msg.strip():
            ctx.reply(msg.strip())

    out.append(button(str(sub.get("label") or "Send"), primary=True, on=send))
    return _wrap(out)


def _table_block(d: dict, ctx: BlockCtx) -> QWidget:  # noqa: ARG001
    _ = ctx
    cols = [str(c) for c in (d.get("columns") or [])[:8]]
    rows = (d.get("rows") or [])[:30]
    out: list[QWidget] = []
    if d.get("caption"):
        out.append(label(str(d["caption"]), muted=True))
    t = QTableWidget(max(0, len(rows)), max(1, len(cols)))
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if cols:
        t.setHorizontalHeaderLabels(cols)
    for r, row in enumerate(rows):
        for c in range(len(cols)):
            cell = row[c] if isinstance(row, list) and c < len(row) else ""
            t.setItem(r, c, QTableWidgetItem(str(cell)))
    t.resizeColumnsToContents()
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    n = max(1, len(rows))
    t.setMinimumHeight(min(220, 44 + n * 30))
    t.setMaximumHeight(min(280, 44 + n * 30))
    out.append(t)
    return _wrap(out)


def _num(v: object) -> str:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "—"
    a = abs(f)
    if a >= 1_000_000:
        return f"{f / 1_000_000:.2f}M"
    if a >= 1000:
        return f"{f / 1000:.1f}k"
    return ("%g" % f)


class BlockChart(QWidget):
    """Small bar/line chart painted with the current palette (like DailyBars)."""

    def __init__(self, kind: str, labels: list[str], series: list[tuple[str, list[float]]]):
        super().__init__()
        self.kind = kind if kind in ("bar", "line") else "bar"
        self.labels = labels[:12]
        self.series = [(name, [float(v) for v in vals][:12]) for name, vals in series[:3]]
        self.setMinimumHeight(150)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = theme.palette()
        cols = [pal["accent"], pal["ok"], pal["warn"]]
        labels, series = self.labels, self.series
        n = max([len(labels)] + [len(v) for _, v in series] + [1])
        mx = max([abs(v) for _, vals in series for v in vals] + [1])
        W, H, pad_l, pad_b, pad_t = self.width(), self.height(), 44, 24, 12
        plot_w = max(10, W - pad_l - 8)
        plot_h = max(10, H - pad_t - pad_b)
        p.setPen(QColor(pal["line"]))
        for g in range(4):
            y = pad_t + plot_h * g / 3
            p.drawLine(pad_l, int(y), W - 8, int(y))
        small = max(8, theme.base_size() - 2)
        p.setPen(QColor(pal["muted"]))
        f = p.font()
        f.setPointSize(small)
        p.setFont(f)
        p.drawText(QRectF(0, pad_t - 2, pad_l - 4, 16), Qt.AlignmentFlag.AlignRight, _num(mx))
        p.drawText(QRectF(0, pad_t + plot_h - 16, pad_l - 4, 16), Qt.AlignmentFlag.AlignRight, "0")
        step = plot_w / n
        for i in range(n):
            x = pad_l + i * step
            if i < len(labels):
                p.setPen(QColor(pal["faint"]))
                p.drawText(QRectF(x, H - pad_b + 2, step, 16), Qt.AlignmentFlag.AlignCenter, str(labels[i])[:10])
            for s, (_, vals) in enumerate(series):
                if i >= len(vals):
                    continue
                h = plot_h * abs(vals[i]) / mx
                y0 = pad_t + plot_h - h
                col = QColor(cols[s % len(cols)])
                if self.kind == "bar":
                    bw = step / (len(series) + 1)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(col)
                    p.drawRoundedRect(QRectF(x + 2 + s * bw, y0, max(2, bw - 2), max(2, h)), 3, 3)
                else:
                    p.setPen(col)
                    if i + 1 < len(vals):
                        h2 = plot_h * abs(vals[i + 1]) / mx
                        p.drawLine(int(x + step / 2), int(y0), int(x + step * 1.5), int(pad_t + plot_h - h2))
                    p.setBrush(col)
                    p.drawEllipse(QRectF(x + step / 2 - 3, y0 - 3, 6, 6))


def _chart_block(d: dict, ctx: BlockCtx) -> QWidget:  # noqa: ARG001
    _ = ctx
    labels = [str(x) for x in (d.get("labels") or [])[:12]]
    series: list[tuple[str, list[float]]] = []
    for s in (d.get("series") or [])[:3]:
        if not isinstance(s, dict):
            continue
        vals = []
        for v in (s.get("values") or [])[:12]:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                vals.append(0.0)
        series.append((str(s.get("name") or ""), vals))
    out: list[QWidget] = []
    if d.get("title"):
        out.append(label(str(d["title"]), h2=True))
    out.append(BlockChart(str(d.get("kind") or "bar"), labels, series))
    if any(name for name, _ in series):
        import html as _html

        p = theme.palette()
        cols = [p["accent"], p["ok"], p["warn"]]
        leg = QLabel("   ".join('<font color="%s">■</font> %s' % (cols[i % len(cols)], _html.escape(name))
                                for i, (name, _) in enumerate(series) if name))
        leg.setTextFormat(Qt.TextFormat.RichText)
        leg.setStyleSheet(f"color: {p['muted']}; font-size: {theme.base_size() - 1}px;")
        out.append(leg)
    return _wrap(out)


def _steps_block(d: dict, ctx: BlockCtx) -> QWidget:  # noqa: ARG001
    _ = ctx
    p = theme.palette()
    glyph = {"done": ("✓", p["ok"]), "running": ("▶", p["accent"]), "error": ("✗", p["bad"])}
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    for it in (d.get("items") or [])[:12]:
        if not isinstance(it, dict) or not it.get("label"):
            continue
        g, col = glyph.get(it.get("status"), ("○", p["faint"]))
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        dot = QLabel(g)
        dot.setStyleSheet(f"color: {col}; font-size: {theme.base_size()}px;")
        dot.setFixedWidth(16)
        h.addWidget(dot)
        h.addWidget(label(str(it["label"])), 1)
        v.addWidget(row)
    return box


def _stats_block(d: dict, ctx: BlockCtx) -> QWidget:  # noqa: ARG001
    _ = ctx
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    items = [it for it in (d.get("items") or []) if isinstance(it, dict) and it.get("value")][:4]
    for it in items or [{"value": "—", "label": ""}]:
        c = card()
        l = QVBoxLayout(c)
        l.setContentsMargins(14, 10, 14, 10)
        val = label(str(it.get("value")), h1=True, wrap=False)
        l.addWidget(val)
        if it.get("label"):
            l.addWidget(label(str(it["label"]), muted=True, wrap=False))
        h.addWidget(c, 1)
    return row


def safe_eval(src: str) -> float:
    """Evaluate a plain arithmetic expression (numbers, + - * / // % ** and parens)."""
    import operator as _op

    ops = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv,
           ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod, ast.Pow: _op.pow}

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        raise ValueError("numbers and + - * / // % ** only")

    return ev(ast.parse((src or "").strip().replace("×", "*").replace("÷", "/").replace(",", ""), mode="eval"))


def _calc_block(d: dict, ctx: BlockCtx) -> QWidget:
    p = theme.palette()
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    if d.get("title"):
        v.addWidget(label(str(d["title"]), h2=True))
    ed = QLineEdit(str(d.get("expression") or ""))
    ed.setPlaceholderText("e.g. 1200*1.2/12")
    res = QLabel("—")
    res.setStyleSheet(f"color: {p['text']}; font-size: {theme.base_size() + 4}px; font-weight: 700;")
    res.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    v.addWidget(ed)
    v.addWidget(res)

    def recalc() -> None:
        try:
            res.setText(("%g" % safe_eval(ed.text())))
        except Exception:  # noqa: BLE001 (blank input, divide by zero, junk)
            res.setText("—")

    ed.textChanged.connect(lambda _t: recalc())
    recalc()
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    h.addWidget(button("Send result", primary=True, on=lambda: ctx.reply(f"{ed.text()} = {res.text()}") if res.text() != "—" else None))
    h.addStretch(1)
    v.addWidget(row)
    return box


_RENDER = {"buttons": _buttons_block, "form": _form_block, "table": _table_block, "chart": _chart_block,
           "steps": _steps_block, "stats": _stats_block, "calculator": _calc_block}


def render_block(data: object, ctx: BlockCtx) -> QWidget:
    """Render one parsed ```ui block (unknown shapes become a quiet note, never a crash)."""
    if isinstance(data, dict) and data.get("type") in _RENDER:
        try:
            w = _RENDER[data["type"]](data, ctx)
            if w is not None:
                return w
        except Exception:  # noqa: BLE001 (a malformed block must not break the message)
            pass
        return label("That interactive block didn't render — ask the Bot to try again.", faint=True)
    return label("Unsupported block type — ask the Bot to use text, buttons, a form, a table, a chart, steps, stats or a calculator.", faint=True)
