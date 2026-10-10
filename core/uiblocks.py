"""Intelligent-UI block protocol: the model answers with interactive widgets, not just text.

A Bot composes a reply from markdown plus fenced ````ui` JSON blocks::

    Some text here.

    ```ui
    {"type": "buttons", "items": [{"label": "Summarise it", "reply": "Summarise the inbox now"}]}
    ```

The desktop client renders each block as a native widget (buttons, forms, tables,
charts, progress steps, stat cards, calculator). Only these seven types exist and
block actions can only send a chat reply or open a link: anything consequential
still goes through tools and the normal approval cards. This module is Qt-free so
the parser stays unit-testable; rendering lives in ui/blocks.py.
"""
from __future__ import annotations

import json
import re

BLOCK_TYPES = ("buttons", "form", "table", "chart", "steps", "stats", "calculator")
MAX_BLOCKS = 4

_FENCE = re.compile(r"```ui[ \t]*\r?\n(.*?)```", re.DOTALL)


def split_message(text: str) -> list[tuple[str, object]]:
    """Split reply text into ("text", str) and ("ui", dict | None) segments.

    A fence whose JSON does not parse yields ("ui", None) so the renderer can
    show the raw fence instead of silently dropping it. At most MAX_BLOCKS ui
    segments are honoured; further fences stay plain text.
    """
    segs: list[tuple[str, object]] = []
    pos, count = 0, 0
    for m in _FENCE.finditer(text or ""):
        if m.start() > pos:
            segs.append(("text", text[pos:m.start()]))
        if count < MAX_BLOCKS:
            try:
                segs.append(("ui", json.loads(m.group(1))))
            except (json.JSONDecodeError, ValueError):
                segs.append(("ui", None))
            count += 1
            pos = m.end()
        else:
            pos = m.start()
            break
    if pos < len(text or ""):
        segs.append(("text", text[pos:]))
    return [(k, v) for k, v in segs if k == "ui" or (isinstance(v, str) and v.strip())]


def plain_text(text: str) -> str:
    """The reply as readable text (ui fences stripped, for copy and search)."""
    joined = "\n\n".join(v for k, v in split_message(text) if k == "text")
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


UI_PROMPT = """# Interactive replies (Intelligent UI)
When the user would be helped by more than prose, compose parts of your reply as interactive widgets using fenced ```ui JSON blocks. Available types (nothing else renders):

- buttons: {"type": "buttons", "items": [{"label": "...", "reply": "message text sent when tapped"}]} — up to 4 quick answers or next actions.
- form: {"type": "form", "title": "...", "fields": [{"key": "name", "label": "Your name", "kind": "text|long|choice|toggle", "options": ["a","b"], "placeholder": "..."}], "submit": {"label": "Send", "message": "Details:\\nName: {name}"}} — {key} placeholders are filled from the answers and sent as one message.
- table: {"type": "table", "columns": ["A","B"], "rows": [["a1","b1"]], "caption": "..."} — comparisons, lists, prices. Max 8 columns, 30 rows.
- chart: {"type": "chart", "kind": "bar|line", "labels": ["Mon","Tue"], "series": [{"name": "...", "values": [1,2]}]} — trends and amounts. Max 12 labels, 3 series.
- steps: {"type": "steps", "items": [{"label": "...", "status": "done|running|pending|error"}]} — plans and progress.
- stats: {"type": "stats", "items": [{"value": "1.27M", "label": "Tokens"}]} — up to 4 headline numbers.
- calculator: {"type": "calculator", "expression": "1200*1.2/12"} — a working calculator for money/math questions the user can tweak.

Rules: valid JSON only (double quotes); short labels; at most 4 blocks per reply; buttons/forms only send chat replies or open links — anything consequential (send, buy, delete, run, schedule) still goes through your tools and the user's approvals, never through a widget. When plain text answers best, use plain text."""
