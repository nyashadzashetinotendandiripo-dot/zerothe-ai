"""Intelligent-UI blocks: protocol parsing, component rendering, chat wiring."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QPushButton, QTableWidget, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841

from core.uiblocks import BLOCK_TYPES, UI_PROMPT, plain_text, split_message  # noqa: E402
from ui.blocks import BlockChart, BlockCtx, render_block, safe_eval  # noqa: E402
from ui.chat_view import ChatPage  # noqa: E402
from ui.store import Store  # noqa: E402
from ui.widgets import ImageCache  # noqa: E402

BTN = 'Pick one:\n```ui\n{"type": "buttons", "items": [{"label": "Alpha", "reply": "picked alpha"}, {"label": "Beta", "reply": "picked beta"}]}\n```\ndone.'


class Protocol(unittest.TestCase):
    def test_plain_text_has_no_segments(self):
        self.assertEqual(split_message("hello *world*"), [("text", "hello *world*")])

    def test_single_block(self):
        segs = split_message(BTN)
        self.assertEqual([k for k, _ in segs], ["text", "ui", "text"])
        self.assertEqual(segs[1][1]["type"], "buttons")

    def test_invalid_json_kept_for_renderer(self):
        segs = split_message('x\n```ui\n{nope\n```\ny')
        kinds = [k for k, _ in segs]
        self.assertIn("ui", kinds)
        self.assertIsNone([v for k, v in segs if k == "ui"][0])

    def test_unclosed_fence_stays_text(self):
        self.assertEqual(split_message("x\n```ui\n{}}}"), [("text", "x\n```ui\n{}}}")])

    def test_json_fences_untouched(self):
        self.assertEqual(split_message('```json\n{"a": 1}\n```'), [("text", '```json\n{"a": 1}\n```')])

    def test_block_cap(self):
        fence = '```ui\n{"type": "stats", "items": [{"value": "1", "label": "x"}]}\n```'
        segs = split_message(("t\n" + fence + "\n") * 6)
        self.assertEqual(sum(1 for k, _ in segs if k == "ui"), 4)

    def test_plain_text_strips_fences(self):
        self.assertEqual(plain_text(BTN), "Pick one:\n\ndone.")

    def test_contract_covers_all_types(self):
        for t in BLOCK_TYPES:
            self.assertIn(t, UI_PROMPT)
        self.assertIn("approvals", UI_PROMPT)


class SafeEval(unittest.TestCase):
    def test_arithmetic(self):
        self.assertAlmostEqual(safe_eval("1200*1.2/12"), 120.0)
        self.assertAlmostEqual(safe_eval("(2 + 3) ** 2 % 4"), 1.0)

    def test_rejects_code(self):
        for bad in ("__import__('os')", "open(1)", "1/0", "abc", "", "len([1])"):
            with self.assertRaises(Exception, msg=bad):
                safe_eval(bad)


def ctx(rec: list) -> BlockCtx:
    return BlockCtx(reply=lambda t: rec.append(("reply", t)), open_url=lambda u: rec.append(("open", u)))


class Render(unittest.TestCase):
    def test_all_types_build(self):
        rec: list = []
        c = ctx(rec)
        blocks = [
            {"type": "buttons", "items": [{"label": "A", "reply": "a"}]},
            {"type": "form", "title": "T", "fields": [{"key": "n", "label": "Name"}, {"key": "c", "label": "Pick", "kind": "choice", "options": ["x", "y"]},
                                                      {"key": "d", "label": "Detail", "kind": "long"}, {"key": "s", "label": "Spam", "kind": "toggle"}],
             "submit": {"label": "Go", "message": "n={n} c={c}"}},
            {"type": "table", "columns": ["A", "B"], "rows": [["1", "2"], ["3", "4"]], "caption": "C"},
            {"type": "chart", "kind": "bar", "labels": ["M", "T"], "series": [{"name": "S", "values": [1, 5]}]},
            {"type": "chart", "kind": "line", "labels": ["M", "T"], "series": [{"values": [2, 3]}]},
            {"type": "steps", "items": [{"label": "a", "status": "done"}, {"label": "b", "status": "running"}, {"label": "c"}]},
            {"type": "stats", "items": [{"value": "9", "label": "N"}]},
            {"type": "calculator", "expression": "2+2*2"},
        ]
        for b in blocks:
            w = render_block(b, c)
            self.assertIsInstance(w, QWidget, b["type"])

    def test_unknown_and_garbage_never_crash(self):
        rec: list = []
        c = ctx(rec)
        for b in ({"type": "teleport"}, {"type": "buttons", "items": "nope"}, "str", None, {"nope": 1}):
            self.assertIsInstance(render_block(b, c), QWidget)

    def test_button_click_replies(self):
        rec: list = []
        w = render_block({"type": "buttons", "items": [{"label": "A", "reply": "picked a"}]}, ctx(rec))
        btns = w.findChildren(QPushButton)
        self.assertTrue(btns)
        btns[0].click()
        self.assertEqual(rec, [("reply", "picked a")])

    def test_button_open(self):
        rec: list = []
        w = render_block({"type": "buttons", "items": [{"label": "Site", "open": "https://example.com"}]}, ctx(rec))
        w.findChildren(QPushButton)[0].click()
        self.assertEqual(rec, [("open", "https://example.com")])

    def test_form_submit_substitutes(self):
        rec: list = []
        w = render_block({"type": "form", "fields": [{"key": "n", "label": "Name"}],
                          "submit": {"message": "hi {n}!"}}, ctx(rec))
        from PySide6.QtWidgets import QLineEdit  # noqa: E402
        w.findChildren(QLineEdit)[0].setText("Jo")
        w.findChildren(QPushButton)[-1].click()
        self.assertEqual(rec, [("reply", "hi Jo!")])

    def test_table_and_chart_shapes(self):
        rec: list = []
        c = ctx(rec)
        t = render_block({"type": "table", "columns": ["A"], "rows": [["1"]] * 50}, c)
        self.assertLessEqual(t.findChildren(QTableWidget)[0].rowCount(), 31)
        ch = render_block({"type": "chart", "labels": ["a"], "series": [{"values": [3]}]}, c)
        self.assertTrue(ch.findChildren(BlockChart))
        ch.findChildren(BlockChart)[0].grab()   # paint must not crash offscreen


class FakeApi:
    def __init__(self) -> None:
        self.posts: list[tuple] = []
        self.conn = type("Conn", (), {"mode": "local", "url": "http://127.0.0.1:9"})()

    def get(self, path, ok, err=None, params=None):
        if path.endswith("/threads"):
            ok([{"id": "t1", "title": "Main", "main": True}])
        elif path.startswith("/api/threads/"):
            ok({"thread": {"id": "t1"}, "items": [], "approvals": []})
        elif path == "/api/commands":
            ok([])
        elif path == "/api/digest":
            ok({"headline": "H"})
        elif path == "/api/updates":
            ok({})
        elif path == "/api/usage":
            ok({"total": 0, "input_tokens": 0, "output_tokens": 0, "resets_at": 0, "limit": 0, "daily": [], "cost": {}, "per_bot": []})
        elif path == "/api/actions":
            ok([])
        else:
            ok({})

    def post(self, path, body, ok, err=None):
        self.posts.append((path, body))
        ok({})

    def put(self, path, body, ok, err=None):
        self.posts.append((path, body))
        ok({})


class ChatWiring(unittest.TestCase):
    def make_page(self) -> tuple[FakeApi, Store, ChatPage]:
        api = FakeApi()
        store = Store(api)  # type: ignore[arg-type]
        page = ChatPage(api, store, ImageCache(api))  # type: ignore[arg-type]
        page.bot_id, page.thread_id = "b1", "t1"
        return api, store, page

    def test_history_message_renders_buttons(self):
        api, _store, page = self.make_page()
        page.apply_item({"type": "assistant", "id": 7, "text": BTN, "ts": 1_700_000_000})
        rows = page.list.box.findChildren(QPushButton)
        labels = [b.text() for b in rows]
        self.assertIn("Alpha", labels)
        before = len(api.posts)
        next(b for b in rows if b.text() == "Beta").click()
        self.assertEqual(len(api.posts), before + 1)
        self.assertEqual(api.posts[-1][1]["text"], "picked beta")

    def test_copy_text_excludes_fence_json(self):
        from ui.chat_view import MessageRow  # noqa: E402
        _api, _store, page = self.make_page()
        page.apply_item({"type": "assistant", "id": 8, "text": BTN, "ts": 1_700_000_000})
        rows = [w for w in page.list.box.findChildren(MessageRow)]
        self.assertTrue(rows)
        got = rows[-1].get_text()
        self.assertIn("Pick one:", got)
        self.assertIn("done.", got)
        self.assertNotIn("picked alpha", got)
        self.assertNotIn('"type": "buttons"', got)

    def test_streamed_finalize_renders_blocks(self):
        api, _store, page = self.make_page()
        page.on_event({"type": "delta", "thread_id": "t1", "stream_id": "s1", "text": "Pick one:\n"})
        page.apply_item({"type": "assistant", "id": 9, "text": BTN, "ts": 1_700_000_000, "stream_id": "s1"})
        labels = [b.text() for b in page.list.box.findChildren(QPushButton)]
        self.assertIn("Alpha", labels)
        self.assertNotIn("s1", page.stream_rows)
        _ = api


if __name__ == "__main__":
    unittest.main()
