"""Phase 3 UI: fades, sidebar reorder guard, theme-switch state preservation, scrollable usage, empty states, density sweep."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QTableWidget, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])  # noqa: F841

from ui import theme  # noqa: E402
from ui.chat_view import ChatPage  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.pages_usage_log import LogPage, UsagePage  # noqa: E402
from ui.store import Store  # noqa: E402
from ui.widgets import ImageCache, Toasts, fade_in, page_layout, table_empty  # noqa: E402


def pump(sec: float) -> None:
    end = time.time() + sec
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.02)


USAGE = {"total": 0, "input_tokens": 0, "output_tokens": 0, "resets_at": time.time() + 86400, "limit": 0,
         "daily": [], "cost": {}, "per_bot": []}


class FakeApi:
    """Permissive stand-in for ui.api.Api (synchronous callbacks)."""

    def __init__(self) -> None:
        self.posts: list[tuple] = []
        self.conn = type("Conn", (), {"mode": "local", "url": "http://127.0.0.1:9"})()

    def get(self, path, ok, err=None, params=None):
        if path.endswith("/threads"):
            ok([{"id": "t1", "title": "Main", "main": True}])
        elif path.startswith("/api/threads/"):
            ok({"thread": {"id": path.rsplit("/", 1)[1]}, "items": [], "approvals": []})
        elif path == "/api/commands":
            ok([])
        elif path == "/api/usage":
            ok(dict(USAGE))
        elif path == "/api/digest":
            ok({"headline": "Test headline"})
        elif path == "/api/updates":
            ok({})
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


def make_store() -> tuple[FakeApi, Store]:
    api = FakeApi()
    return api, Store(api)  # type: ignore[arg-type]


def make_win() -> tuple[FakeApi, Store, MainWindow]:
    api, store = make_store()
    win = MainWindow(api, store, tray_available=False)  # type: ignore[arg-type]
    win.banner_shown = True   # keep the browser-engine prompt from ever popping in tests
    return api, store, win


BOT = {"id": "b1", "name": "Inbox", "emoji": "📥", "paused": False, "job": "Triage mail"}


class Fades(unittest.TestCase):
    def test_fade_in_detaches_effect(self):
        w = QLabel("hi")
        fade_in(w)
        self.assertIsNotNone(w.graphicsEffect())
        pump(0.4)
        self.assertIsNone(w.graphicsEffect())

    def test_toast_fades_out_then_removed(self):
        host = QWidget()
        host.resize(800, 600)
        host.show()
        toasts = Toasts(host)
        toasts.show("hello", ms=60)
        self.assertEqual(len(toasts.items), 1)
        self.assertIsNotNone(toasts.items[0].graphicsEffect())
        pump(0.8)   # 60ms display + 150ms fade-out
        self.assertEqual(len(toasts.items), 0)


class EmptyTables(unittest.TestCase):
    def test_placeholder_row(self):
        t = QTableWidget()
        t.setColumnCount(3)
        table_empty(t, "Nothing here yet.")
        self.assertEqual(t.rowCount(), 1)
        self.assertEqual(t.columnSpan(0, 0), 3)
        self.assertEqual(t.item(0, 0).text(), "Nothing here yet.")
        self.assertIsNone(t.item(0, 0).data(0x100))   # Qt.UserRole: no payload

    def test_noop_with_rows(self):
        t = QTableWidget()
        t.setColumnCount(3)
        t.setRowCount(1)
        table_empty(t, "Nothing here yet.")
        self.assertEqual(t.columnSpan(0, 0), 1)

    def test_usage_empty_and_scrollable(self):
        api, store = make_store()
        page = UsagePage(api, store)  # type: ignore[arg-type]
        self.assertIsNotNone(page.findChild(QScrollArea))
        page.load()
        self.assertEqual(page.table.rowCount(), 1)
        self.assertIn("No usage", page.table.item(0, 0).text())
        self.assertEqual(page.prices.rowCount(), 1)
        page.save_prices()   # placeholder rows must not corrupt the save
        self.assertEqual(page.price_msg.text(), "Saved.")

    def test_log_empty_and_detail_safe(self):
        api, store = make_store()
        page = LogPage(api, store)  # type: ignore[arg-type]
        page.load()
        self.assertEqual(page.table.rowCount(), 1)
        self.assertIn("No actions", page.table.item(0, 0).text())
        page.detail()   # must not raise on the placeholder row


class DensityText(unittest.TestCase):
    def tearDown(self) -> None:
        theme.configure("indigo", "comfortable", "default")

    def test_chat_title_follows_text_size(self):
        theme.configure(text="large")
        api, store = make_store()
        page = ChatPage(api, store, ImageCache(api))  # type: ignore[arg-type]
        self.assertIn(f"{theme.base_size() + 2}px", page.title.styleSheet())

    def test_page_margins_follow_density(self):
        theme.configure(density="compact")
        w = QWidget()
        lay = page_layout(w)
        m = lay.contentsMargins()
        self.assertEqual((m.left(), m.top(), m.right(), m.bottom()),
                         (theme.dp(32), theme.dp(26), theme.dp(32), theme.dp(20)))


class SidebarChurn(unittest.TestCase):
    def test_rows_reused_and_reorder_guarded(self):
        _api, store, win = make_win()
        store.bots = [dict(BOT), {"id": "b2", "name": "Chief", "emoji": "🧭", "paused": False, "job": ""}]
        win.refresh_lists()
        first = dict(win.rows)
        self.assertIn("bot:b1", first)
        calls: list = []
        box = win.bots_box
        orig = box.insertWidget
        box.insertWidget = lambda *a: (calls.append(a), orig(*a))  # type: ignore[method-assign]
        try:
            win.refresh_lists()
        finally:
            box.insertWidget = orig  # type: ignore[method-assign]
        self.assertEqual(calls, [])   # same order: no layout churn
        self.assertIs(win.rows["bot:b1"], first["bot:b1"])   # rows reused, not recreated
        win._pins = {"b2"}
        win.refresh_lists()
        order = [win.bots_box.itemAt(i).widget() for i in range(win.bots_box.count())]
        self.assertEqual(order, [win.rows["bot:b2"], win.rows["bot:b1"]])


class ThemeSwitchState(unittest.TestCase):
    def test_snapshot_restore_roundtrip(self):
        _api, store, win = make_win()
        store.bots = [dict(BOT)]
        win.select("page:home")
        win.chat.bot_id, win.chat.thread_id = "b1", "t1"
        win.chat.input.setPlainText("draft text")
        snap = win.snapshot()
        self.assertEqual(snap["key"], "page:home")
        self.assertEqual(snap["draft"], "draft text")
        _api2, store2 = make_store()
        store2.bots = [dict(BOT)]
        win2 = MainWindow(_api2, store2, tray_available=False)  # type: ignore[arg-type]
        win2.banner_shown = True
        win2.show()
        win2.restore({"key": "bot:b1", "thread": "t1", "draft": "hello", "panel": True,
                      "list_scroll": 0, "side_scroll": 0})
        self.assertEqual(win2.current_key, "bot:b1")
        self.assertEqual(win2.chat.thread_id, "t1")
        self.assertEqual(win2.chat.input.toPlainText(), "hello")
        self.assertTrue(win2.chat.activity.isVisible())


if __name__ == "__main__":
    unittest.main()
