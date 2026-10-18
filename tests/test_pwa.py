"""The phone web app in a real (mobile-sized) Chromium: sign in with the link, open a Bot, use slash commands."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from core.engine import Engine  # noqa: E402
from service.server import create_app  # noqa: E402

SHOTS = os.environ.get("PWA_SHOTS")
WEB = Path(__file__).resolve().parent.parent / "web"


class PwaInstallable(unittest.TestCase):
    def test_manifest_has_android_icons(self):
        import json
        m = json.loads((WEB / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m.get("display"), "standalone")
        png = [i for i in m["icons"] if i.get("type") == "image/png"]
        self.assertTrue(any(i.get("sizes") == "192x192" for i in png), "need a 192px icon for Android")
        self.assertTrue(any(i.get("sizes") == "512x512" for i in png), "need a 512px icon for Android")
        self.assertTrue(any("maskable" in (i.get("purpose") or "") for i in png), "need a maskable icon")
        for i in png:
            self.assertTrue((WEB / i["src"].lstrip("/")).is_file(), i["src"])

    def test_service_worker_caches_icons(self):
        sw = (WEB / "sw.js").read_text(encoding="utf-8")
        self.assertIn("/icon-192.png", sw)
        self.assertIn("/icon-512.png", sw)


class PwaCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-pwa-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.start()
        cls.port, cls.token = 18769, "pwa-token"
        cls.server = uvicorn.Server(uvicorn.Config(create_app(cls.eng, cls.token), host="127.0.0.1", port=cls.port, log_level="warning"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        time.sleep(1.5)
        cls.bot = cls.eng.bots.create("Inbox", job="Triage email", emoji="📥")
        cls.eng.threads.main_thread(cls.bot["id"])
        cls.eng.memory.add(cls.bot["id"], "preference", "Keep summaries to one screen", pinned=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.eng.stop()

    def test_commands_on_the_phone(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(f"http://127.0.0.1:{self.port}/")
            pg.wait_for_selector("#email")
            pg.fill("#email", "pwa@test.dev")
            pg.fill("#token", "pwa pass 123")
            pg.fill("#token2", "pwa pass 123")
            pg.click("#login-btn")
            pg.wait_for_selector("[data-bot]")
            pg.click("[data-bot]")
            pg.wait_for_selector("#input")

            pg.fill("#input", "/")
            pg.wait_for_selector("#cmdlist button")
            self.assertGreaterEqual(pg.locator("#cmdlist button").count(), 5)
            pg.fill("#input", "/mem")
            self.assertEqual(pg.locator("#cmdlist button").first.get_attribute("data-cmd"), "memory")
            if SHOTS:
                pg.screenshot(path=f"{SHOTS}/phone-popup.png")
            pg.locator("#cmdlist button").first.click()                 # tap to complete
            self.assertEqual(pg.input_value("#input"), "/memory ")
            self.assertTrue(pg.locator("#cmdlist").is_hidden())

            pg.click("#send")                                            # tap Send
            pg.wait_for_selector(".notice.cmd")
            self.assertIn("Keep summaries to one screen", pg.inner_text(".notice.cmd"))

            pg.fill("#input", "/status")
            pg.click("#send")
            pg.wait_for_function("document.querySelectorAll('.notice.cmd').length >= 2")
            self.assertIn("Inbox", pg.inner_text("#messages"))
            if SHOTS:
                pg.screenshot(path=f"{SHOTS}/phone-output.png")

            pg.fill("#input", "/new Phone plan")
            pg.click("#send")
            pg.wait_for_function("!document.querySelector('.notice.cmd')")   # switched to the fresh thread
            self.assertEqual(len([t for t in self.eng.threads.list_for_bot(self.bot["id"]) if t["title"] == "Phone plan"]), 1)
            self.assertEqual(errs, [])
            b.close()

    def test_intelligent_ui_blocks_render_and_reply(self):
        from playwright.sync_api import sync_playwright
        blk = 'Pick one:\n```ui\n{"type": "buttons", "items": [{"label": "Alpha", "reply": "picked alpha"}]}\n```\ndone.'
        tid = self.eng.threads.main_thread(self.bot["id"])["id"]
        self.eng.threads.add(tid, "Inbox", "assistant", blk, publish=False)
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(f"http://127.0.0.1:{self.port}/")
            pg.wait_for_selector("#email")
            pg.fill("#email", "pwa@test.dev")
            pg.fill("#token", "pwa pass 123")
            if pg.is_visible("#token2"):
                pg.fill("#token2", "pwa pass 123")
            pg.click("#login-btn")
            pg.wait_for_selector("[data-bot]")
            pg.click("[data-bot]")
            pg.wait_for_selector("#messages .ui-buttons button.uib")
            self.assertIn("Alpha", pg.inner_text("#messages .ui-buttons"))
            self.assertNotIn('"type": "buttons"', pg.inner_text("#messages"))   # raw fence JSON is never shown
            if SHOTS:
                pg.screenshot(path=f"{SHOTS}/phone-intelligent-ui.png")
            pg.click("#messages .ui-buttons button.uib")                        # tapping a widget sends its reply
            pg.wait_for_function("document.querySelector('#messages').innerText.includes('picked alpha')")
            self.assertEqual(errs, [])
            b.close()


if __name__ == "__main__":
    unittest.main()
