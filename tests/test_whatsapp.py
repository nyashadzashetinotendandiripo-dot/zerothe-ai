"""WhatsApp Web plugin tests with a fake browser tab.  python -m unittest tests.test_whatsapp -v"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-wa-"))

from core.tooling import ToolResult  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("whatsapp_plugin", ROOT / "plugins" / "whatsapp" / "plugin.py")
wa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wa)


class FakeBrowserError(Exception):
    pass


class FakeScreen:
    def __init__(self, url="about:blank"):
        self.takeover = False
        self.url = url
        self.calls = []
        self.snaps = []
        self.texts = {}

    async def goto(self, url):
        self.calls.append(("goto", url))
        self.url = url
        return {"url": url}

    async def state(self):
        return {"url": self.url}

    async def snapshot(self, text_chars=5000):
        self.calls.append(("snapshot",))
        if self.snaps:
            return dict(self.snaps.pop(0))
        return {"url": self.url, "text": "", "elements": []}

    async def read_text(self, selector=None, max_chars=15000):
        self.calls.append(("read_text", selector))
        if selector not in self.texts:
            raise FakeBrowserError(f"Could not read text: strict mode violation, no element {selector}")
        return {"url": self.url, "text": self.texts[selector], "truncated": False}

    async def click(self, *, element=None, x=None, y=None, double=False, right=False):
        self.calls.append(("click", element))
        return {"url": self.url}

    async def type_text(self, text, *, element=None, clear=True, submit=False):
        self.calls.append(("type", element, text, submit))
        return {"url": self.url}

    async def press(self, key):
        self.calls.append(("press", key))
        return {"url": self.url}

    async def wait_for(self, text, seconds):
        self.calls.append(("wait", text))
        return {"url": self.url}


class FakeBrowser:
    def __init__(self, scr):
        self._scr = scr

    def screen(self, bot_id):
        return self._scr

    def call(self, fn, *args, **kwargs):
        kwargs.pop("timeout", None)
        return asyncio.run(fn(*args, **kwargs))


def ctx_with(scr):
    br = FakeBrowser(scr)
    eng = SimpleNamespace(computer=SimpleNamespace(browser=br))
    return SimpleNamespace(engine=eng, bot={"id": "bot1"})


def el(i, label, role="button", tag="div"):
    return {"id": i, "tag": tag, "role": role, "label": label}


PAIRED_SNAP = {"url": "https://web.whatsapp.com/", "text": "Search\nChats\nMom\nDad",
               "elements": [el(1, "Search input textbox", role="searchbox", tag="input"),
                            el(2, "Mom\n10:20\nsee you soon", role="row"),
                            el(3, "Dad\n09:11\nhi", role="row")]}
QR_SNAP = {"url": "https://web.whatsapp.com/", "text": "Use WhatsApp on your computer\nScan to log in",
           "elements": []}


class StatusTests(unittest.TestCase):
    def test_paired(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(PAIRED_SNAP)]
        out = wa.wa_status(ctx_with(scr), {})
        self.assertIsInstance(out, ToolResult)
        self.assertIn("paired and ready", out.text)
        self.assertIn("Mom", out.text)

    def test_qr_screen(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(QR_SNAP)]
        out = wa.wa_status(ctx_with(scr), {})
        self.assertIn("NOT PAIRED", out.text)
        self.assertIn("scan the QR code", out.text)

    def test_navigates_to_whatsapp_first(self):
        scr = FakeScreen("about:blank")
        scr.snaps = [dict(PAIRED_SNAP)]
        wa.wa_status(ctx_with(scr), {})
        self.assertIn(("goto", "https://web.whatsapp.com/"), scr.calls)


class OpenChatTests(unittest.TestCase):
    def _ctx(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(PAIRED_SNAP),
                     {"url": scr.url, "text": "Search\nMom", "elements": [el(1, "Search input textbox", role="searchbox", tag="input")]},
                     {"url": scr.url, "text": "Mom", "elements": [el(5, "Mom\n10:21\nsee you soon", role="row")]}]
        return ctx_with(scr), scr

    def test_found(self):
        ctx, scr = self._ctx()
        out = wa.wa_open_chat(ctx, {"chat": "mom"})
        self.assertIn("Opened WhatsApp chat with 'Mom'", out.text)
        self.assertIn(("click", 5), scr.calls)

    def test_missing_lists_visible_chats(self):
        ctx, scr = self._ctx()
        out = wa.wa_open_chat(ctx, {"chat": "Nobody"})
        self.assertIn("No WhatsApp chat matches 'Nobody'", out.text)

    def test_needs_chat_name(self):
        ctx, _scr = self._ctx()
        self.assertIn("needs", wa.wa_open_chat(ctx, {}).text)


class ReadTests(unittest.TestCase):
    def test_direction_aware_merge(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(PAIRED_SNAP)]
        scr.texts = {".message-in >> nth=0": "Hey are you coming?\n10:24",
                     ".message-out >> nth=0": "On my way!\n10:26"}
        out = wa.wa_read(ctx_with(scr), {})
        self.assertIn("[10:24] Them: Hey are you coming?", out.text)
        self.assertIn("[10:26] You: On my way!", out.text)
        self.assertLess(out.text.index("10:24"), out.text.index("10:26"))

    def test_group_sender(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(PAIRED_SNAP), dict(PAIRED_SNAP),
                     {"url": scr.url, "text": "Team", "elements": [el(6, "Team\n11:02\nNyasha: Meeting", role="row")]}]
        scr.texts = {".message-in >> nth=0": "Nyasha\nMeeting at 3\n11:02"}
        out = wa.wa_read(ctx_with(scr), {"chat": "Team"})
        self.assertIn("[11:02] Nyasha: Meeting at 3", out.text)

    def test_panel_fallback(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(PAIRED_SNAP)]
        scr.texts = {"#main": "See you\n10:30\n\nSounds good\n10:32"}
        out = wa.wa_read(ctx_with(scr), {})
        self.assertIn("[10:30]", out.text)
        self.assertIn("layout", out.text)

    def test_empty_chat(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(PAIRED_SNAP)]
        scr.texts = {}
        out = wa.wa_read(ctx_with(scr), {})
        self.assertIn("Could not read", out.text)


class SendTests(unittest.TestCase):
    def _ctx(self, sent="Hello Mom"):
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.snaps = [dict(PAIRED_SNAP),
                     {"url": scr.url, "text": "Search\nMom", "elements": [el(1, "Search input textbox", role="searchbox", tag="input")]},
                     {"url": scr.url, "text": "Mom", "elements": [el(5, "Mom\n10:21\nhi", role="row")]},
                     {"url": scr.url, "text": "Mom\nType a message",
                      "elements": [el(9, "Type a message", role="textbox")]}]
        scr.texts = {".message-out >> nth=0": f"{sent}\n10:31"}
        return ctx_with(scr), scr

    def test_send_verified(self):
        ctx, scr = self._ctx()
        out = wa.wa_send(ctx, {"chat": "Mom", "text": "Hello Mom"})
        self.assertIn("Sent to 'Mom'", out.text)
        types = [c for c in scr.calls if c[0] == "type"]
        self.assertTrue(any(t[2] == "Hello Mom" and t[3] is True for t in types), types)

    def test_send_needs_text(self):
        ctx, _scr = self._ctx()
        self.assertIn("Nothing to send", wa.wa_send(ctx, {}).text)

    def test_send_risk_and_screen_flags(self):
        tools = {t["name"]: t for t in wa.register(None)}
        self.assertEqual(tools["send"].get("risk"), "send")
        self.assertNotIn("risk", tools["read"])
        self.assertTrue(all(t.get("needs_screen") for t in tools.values()))


class BuildTests(unittest.TestCase):
    def test_build_python_maps_tools(self):
        from core.plugins import build_python
        manifest = {"id": "whatsapp", "name": "WhatsApp Web", "entry": "plugin.py"}
        conn = SimpleNamespace(cfg=lambda key, default="": default)
        specs = build_python(ROOT / "plugins" / "whatsapp", manifest)(conn)
        by_name = {s.name: s for s in specs}
        self.assertEqual(set(by_name), {"whatsapp_status", "whatsapp_chats", "whatsapp_open_chat",
                                        "whatsapp_read", "whatsapp_send",
                                        "whatsapp_send_media", "whatsapp_reply_to_message", "whatsapp_react_message",
                                        "whatsapp_forward_message", "whatsapp_delete_message",
                                        "whatsapp_mark_unread", "whatsapp_mute_chat", "whatsapp_archive_chat",
                                        "whatsapp_pin_chat", "whatsapp_search_messages", "whatsapp_view_contact"})
        self.assertTrue(all(s.needs_screen for s in specs), "whatsapp tools drive the tab")
        self.assertIsNone(by_name["whatsapp_read"].risk)
        self.assertIsNotNone(by_name["whatsapp_send"].risk)
        self.assertTrue(by_name["whatsapp_status"].read_only)


class HelperNamesTests(unittest.TestCase):
    def test_no_missing_runtime_names(self):
        for name in ("time", "random", "Path", "_SEND_TIMES", "_pause", "_chunks",
                     "_human_type", "_rate_limit", "_think"):
            self.assertTrue(hasattr(wa, name), f"whatsapp plugin must define {name}")


class FakeKeyboard:
    def __init__(self):
        self.typed = []
        self.pressed = []

    def type(self, text, delay=0):
        self.typed.append((text, delay))

    def press(self, key):
        self.pressed.append(key)


class FakeLoc:
    def __init__(self, keyboard):
        self.keyboard = keyboard
        self.clicked = 0

    def click(self, timeout=None):
        self.clicked += 1
        return None

    first = property(lambda self: self)


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()
        self._loc = FakeLoc(self.keyboard)
        self.selector = ""

    def locator(self, sel):
        self.selector = sel
        return self._loc


class HumanTypeTests(unittest.TestCase):
    def test_chunks_rejoin_within_bounds(self):
        import random as pyrandom
        pyrandom.seed(7)
        for text in ("Hello Mom, see you at six!", "x", "A much longer advert text with numbers 123 and emojis here"):
            parts = wa._chunks(text)
            self.assertEqual("".join(parts), text)
            self.assertTrue(all(1 <= len(p) <= 14 for p in parts), parts)
            self.assertTrue(all(len(p) >= 2 for p in parts[:-1]), parts)
        self.assertEqual(wa._chunks(""), [])

    def test_pause_uses_random_span(self):
        import unittest.mock as mock
        with mock.patch.object(wa.time, "sleep") as slp:
            wa._pause(0.6, 1.4)
        slp.assert_called_once()
        self.assertTrue(0.6 <= slp.call_args[0][0] <= 1.4)

    def test_fallback_without_page(self):
        scr = FakeScreen("https://web.whatsapp.com/")
        ctx = ctx_with(scr)
        wa._human_type(ctx.engine.computer.browser, scr, 9, "Hello Mom", submit=True)
        self.assertIn(("type", 9, "Hello Mom", True), scr.calls)

    def test_human_path_types_in_bursts(self):
        import random as pyrandom
        import unittest.mock as mock
        pyrandom.seed(11)
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.page = FakePage()
        with mock.patch.object(wa.time, "sleep"):
            wa._type_direct(scr, 9, "Hello Mom, see you soon", True, 30, 120, 0.05, 0.5, 0.12)
        kb = scr.page.keyboard
        self.assertEqual("".join(t for t, _d in kb.typed), "Hello Mom, see you soon")
        self.assertTrue(len(kb.typed) > 1, "must type in bursts, not one paste")
        delays = {d for _t, d in kb.typed}
        self.assertTrue(len(delays) > 1, "key delays must vary")
        self.assertTrue(all(30 <= d <= 120 for d in delays))
        self.assertIn("Control+A", kb.pressed)
        self.assertIn("Delete", kb.pressed)
        self.assertEqual(kb.pressed[-1], "Enter")
        self.assertIn('[data-gb-id="9"]', scr.page.selector)

    def test_human_path_no_submit(self):
        import random as pyrandom
        import unittest.mock as mock
        pyrandom.seed(3)
        scr = FakeScreen("https://web.whatsapp.com/")
        scr.page = FakePage()
        with mock.patch.object(wa.time, "sleep"):
            wa._type_direct(scr, 4, "mom", False, 12, 45, 0.03, 0.2, 0.05)
        kb = scr.page.keyboard
        self.assertEqual("".join(t for t, _d in kb.typed), "mom")
        self.assertNotIn("Enter", kb.pressed)


class ParseTests(unittest.TestCase):
    def test_split_time_24h(self):
        mins, body = wa._split_time("see you\n18:05")
        self.assertEqual((mins, body), (18 * 60 + 5, "see you"))

    def test_split_time_12h(self):
        mins, body = wa._split_time("ok\n6:05 PM")
        self.assertEqual((mins, body), (18 * 60 + 5, "ok"))

    def test_split_time_missing(self):
        self.assertEqual(wa._split_time("just text"), (None, "just text"))

    def test_ticks_stripped(self):
        _mins, body = wa._split_time("thanks \u2713\u2713\n09:00")
        self.assertEqual(body, "thanks")


if __name__ == "__main__":
    unittest.main()
