"""Phase 2 chat metadata: timestamps, hover actions, citation chips, honest status, send recovery."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841

from ui import theme  # noqa: E402
from ui.chat_view import ChatPage, MessageRow, _extract_citations, _fmt_ts  # noqa: E402
from ui.store import Store  # noqa: E402
from ui.widgets import ImageCache  # noqa: E402

TS = 1_700_000_000


class FakeApi:
    """Just enough of ui.api.Api for ChatPage: records calls, can fail the next post."""

    def __init__(self) -> None:
        self.posts: list[tuple] = []
        self.fail_next = False

    def get(self, path, ok, err=None):
        if path.endswith("/threads"):
            ok([])
        elif path == "/api/commands":
            ok([])
        else:
            ok({})

    def post(self, path, body, ok, err=None):
        self.posts.append((path, body))
        if self.fail_next:
            self.fail_next = False
            (err or (lambda e: None))("service unavailable")
        else:
            ok({})


def make_page() -> tuple[FakeApi, Store, ChatPage]:
    api = FakeApi()
    store = Store(api)  # type: ignore[arg-type]
    page = ChatPage(api, store, ImageCache(api))  # type: ignore[arg-type]
    page.bot_id, page.thread_id = "b1", "t1"
    return api, store, page


class Timestamps(unittest.TestCase):
    def test_missing_or_bad(self):
        for v in (None, "", 0, "abc", float("nan")):
            self.assertEqual(_fmt_ts(v), "", repr(v))

    def test_seconds_and_millis_agree(self):
        expect = datetime.fromtimestamp(TS).strftime("%H:%M")
        self.assertEqual(_fmt_ts(TS), expect)
        self.assertEqual(_fmt_ts(TS * 1000), expect)

    def test_absurd_value_is_blank(self):
        self.assertEqual(_fmt_ts(1e18), "")


class Citations(unittest.TestCase):
    def test_links_extracted_and_deduped(self):
        text = "Try [Zapier](https://zapier.com) or [Zapier again](https://zapier.com) plus [n](https://example.com/a?x=1)"
        self.assertEqual(_extract_citations(text),
                         [("Zapier", "https://zapier.com"), ("n", "https://example.com/a?x=1")])

    def test_only_http_and_newlines_cleaned(self):
        text = "[bad](mailto:x@y.z) [ok](https://s.io)\n[anchor](#top) and [two words](https://t.io)"
        self.assertEqual(_extract_citations(text), [("ok", "https://s.io"), ("two words", "https://t.io")])

    def test_limit(self):
        text = " ".join(f"[t{i}](https://e{i}.com)" for i in range(10))
        self.assertEqual(len(_extract_citations(text)), 6)
        self.assertEqual(len(_extract_citations(text, limit=2)), 2)

    def test_empty(self):
        self.assertEqual(_extract_citations(""), [])
        self.assertEqual(_extract_citations("no links here"), [])


class MessageRowTests(unittest.TestCase):
    def test_meta_row(self):
        row = MessageRow(QLabel("hi"), "user", lambda: "hi", ts=TS, on_edit=lambda: None)
        self.assertEqual(row.time.text(), datetime.fromtimestamp(TS).strftime("%H:%M"))
        self.assertEqual(len(row.btns), 2)          # copy + edit
        a = MessageRow(QLabel("yo"), "assistant", lambda: "yo", on_retry=lambda: None)
        self.assertEqual(len(a.btns), 2)            # copy + retry

    def test_icons_reveal_on_hover_only(self):
        row = MessageRow(QLabel("hi"), "user", lambda: "hi", on_edit=lambda: None)
        self.assertTrue(all(b.icon().isNull() for b, _ in row.btns))
        row._hovered = True
        row._sync_icons()
        self.assertTrue(all(not b.icon().isNull() for b, _ in row.btns))
        row._hovered = False
        row._sync_icons()
        self.assertTrue(all(b.icon().isNull() for b, _ in row.btns))

    def test_copy_places_text_on_clipboard(self):
        row = MessageRow(QLabel("x"), "user", lambda: "the message", on_edit=lambda: None)
        row._copy()
        self.assertEqual(_app.clipboard().text(), "the message")

    def test_citations_render_link_labels(self):
        row = MessageRow(QLabel("x"), "assistant", lambda: "x")
        row.set_citations([("Docs", "https://docs.example.com")])
        lbs = [w for w in row.findChildren(QLabel) if w.property("cite")]
        self.assertEqual(len(lbs), 1)
        self.assertIn("https://docs.example.com", lbs[0].toolTip())
        self.assertIn("Docs", lbs[0].text())
        row.set_citations([])                        # no-op, doesn't duplicate
        self.assertEqual(len([w for w in row.findChildren(QLabel) if w.property("cite")]), 1)


class HonestStatus(unittest.TestCase):
    def tearDown(self):
        theme.set_theme("dark")

    def test_offline_says_reconnecting(self):
        _, store, page = make_page()
        store.connected = False
        page.update_status()
        self.assertEqual(page.status.text(), "Reconnecting…")

    def test_idle(self):
        _, store, page = make_page()
        store.connected = True
        page.update_status()
        self.assertEqual(page.status.text(), "Idle")

    def test_thinking_before_first_step_then_working(self):
        _, store, page = make_page()
        store.connected = True
        store.busy["b1"] = {"thread_id": "t1", "status": "running", "steps": 0, "turn_id": "r1"}
        page.update_status()
        self.assertEqual(page.status.text(), "Thinking…")
        store.busy["b1"]["steps"] = 3
        page.update_status()
        self.assertEqual(page.status.text(), "Working")

    def test_waiting_approval_still_honest_offline(self):
        _, store, page = make_page()
        store.connected = False
        store.busy["b1"] = {"thread_id": "t1", "status": "waiting_approval", "steps": 1, "turn_id": "r1"}
        page.update_status()
        self.assertEqual(page.status.text(), "Reconnecting…")


class SendRecovery(unittest.TestCase):
    def test_failure_shows_retry_card_and_retry_resends(self):
        api, _, page = make_page()
        page.input.setPlainText("hello there")
        api.fail_next = True
        page.send()
        self.assertEqual(page.last_user_text, "hello there")
        self.assertEqual(len(api.posts), 1)
        retries = [b for b in page.findChildren(QPushButton) if b.text() == "Retry"]
        self.assertEqual(len(retries), 1, "an inline retry card should be visible")
        retries[0].click()
        self.assertEqual(len(api.posts), 2)
        self.assertEqual(api.posts[1][1]["text"], "hello there")
        retries = [b for b in page.findChildren(QPushButton) if b.text() == "Retry"]
        self.assertEqual(retries, [], "the failure card should be gone after a successful retry")

    def test_success_sends_without_card(self):
        api, _, page = make_page()
        page.input.setPlainText("ok")
        page.send()
        self.assertEqual(len(api.posts), 1)
        self.assertEqual([b for b in page.findChildren(QPushButton) if b.text() == "Retry"], [])


class Rendering(unittest.TestCase):
    def test_apply_item_builds_rows_with_ts_and_cites(self):
        _, _, page = make_page()
        page.apply_item({"type": "user", "id": 1, "text": "ping", "images": [], "ts": TS})
        page.apply_item({"type": "assistant", "id": 2, "text": "See [A](https://a.example) and [B](https://b.example)", "ts": TS + 60})
        rows = page.findChildren(MessageRow)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].time.text(), datetime.fromtimestamp(TS).strftime("%H:%M"))
        cites = [w for w in rows[1].findChildren(QLabel) if w.property("cite")]
        self.assertEqual(len(cites), 2)
        self.assertEqual(page.last_user_text, "ping")

    def test_stream_finalize_sets_ts_and_cites(self):
        _, _, page = make_page()
        page.on_event({"type": "delta", "thread_id": "t1", "stream_id": "s1", "text": "partial"})
        self.assertIn("s1", page.streams)
        page.apply_item({"type": "assistant", "id": 9, "stream_id": "s1", "text": "done [x](https://x.example)", "ts": TS + 5})
        row = page.findChildren(MessageRow)[0]
        self.assertEqual(row.time.text(), datetime.fromtimestamp(TS + 5).strftime("%H:%M"))
        self.assertEqual(len([w for w in row.findChildren(QLabel) if w.property("cite")]), 1)
        self.assertNotIn("s1", page.stream_rows)

    def test_qss_has_new_rules(self):
        s = theme.qss()
        self.assertIn('small="true"', s)
        self.assertIn('cite="true"', s)


if __name__ == "__main__":
    unittest.main()
