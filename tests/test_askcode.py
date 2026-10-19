"""The MFA bridge: ask_code gets a one-time code from the user through the approval card and
types it directly into the browser page. The code never reaches the model context.

Run: python -m unittest tests.test_askcode -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-askcode-")

from core.engine import Engine  # noqa: E402
from core.providers import LLMResult, ToolCall  # noqa: E402
from test_engine import FakeProvider, wait_for  # noqa: E402

import core.agent as agent_mod  # noqa: E402

CODE = "492 816"


class AskCodeTests(unittest.TestCase):
    fill_ok = True

    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-askcode-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        AskCodeTests.fill_ok = True
        self.bot = self.eng.bots.create(f"Code Bot {uuid.uuid4().hex[:8]}")
        self.th = self.eng.threads.main_thread(self.bot["id"])
        self.captured: dict = {}

        # fake browser: report the current page and capture the injected code
        self.br = self.eng.computer.browser
        self._orig_screen, self._orig_call = self.br.screen, self.br.call

        class _Scr:
            def otp_fill(_self, code: str, press_enter: bool = True) -> dict:
                self.captured["code"] = code
                self.captured["press_enter"] = press_enter
                return {"filled": type(self).fill_ok, "field": "input[name*=code i]:visible" if type(self).fill_ok else "", "pressed": True}

        self.br.screen = lambda bid: _Scr()

        def fake_call(fn, *a, **k):
            name = getattr(fn, "__name__", "")
            if name == "_current_url":
                return "https://portal.example.com/login/otp"
            if name == "otp_fill":
                self.captured["code"] = a[0]
                self.captured["press_enter"] = a[1] if len(a) > 1 else True
                ok = type(self).fill_ok
                return {"filled": ok, "field": "input[name*=code i]:visible" if ok else "", "pressed": True}
            return {"filled": type(self).fill_ok, "pressed": True}
        self.br.call = fake_call

    def tearDown(self):
        self.br.screen, self.br.call = self._orig_screen, self._orig_call

    def use(self, script):
        fake = FakeProvider(script)
        agent_mod.make_provider = lambda *a, **k: fake
        import core.approvals as ap
        ap.make_provider = lambda *a, **k: fake
        return fake

    def start_code(self):
        self.use([
            LLMResult(tool_calls=[ToolCall("t1", "ask_code",
                                           {"question": "Enter the 6-digit code we texted you"})]),
            LLMResult(text="done."),
        ])
        self.eng.send_user_message(self.th["id"], "finish the login")

    def wait_approval(self) -> dict:
        ok = wait_for(lambda: bool(self.eng.approvals.list("pending", self.bot["id"])))
        self.assertTrue(ok, "a code ask should be pending")
        return self.eng.approvals.list("pending", self.bot["id"])[0]

    def wait_turn_done(self):
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(self.bot["id"]), 30), "turn should finish")

    def llm_blob(self) -> str:
        return "\n".join(r["content"] for r in self.eng.threads.rows(self.th["id"]) if r.get("kind") == "llm")

    def test_code_types_into_page_and_never_leaks(self):
        self.start_code()
        a = self.wait_approval()
        self.assertEqual(a["category"], "login")
        self.assertEqual(a["tool"], "ask_code")
        self.eng.approvals.decide(a["id"], True, answer=CODE)
        self.wait_turn_done()
        self.assertEqual(self.captured.get("code"), CODE)
        blob = self.llm_blob()
        self.assertIn("typed the verification code", blob)
        self.assertNotIn(CODE, blob)
        for row in self.eng.actions.list(self.bot["id"]):
            self.assertNotIn(CODE, __import__("json").dumps(row))

    def test_denied_is_blocked(self):
        self.start_code()
        a = self.wait_approval()
        self.eng.approvals.decide(a["id"], False)
        self.wait_turn_done()
        self.assertNotIn("code", self.captured)
        self.assertIn("blocked", self.llm_blob().lower())

    def test_approved_without_code_is_rejected(self):
        self.start_code()
        a = self.wait_approval()
        self.eng.approvals.decide(a["id"], True, answer="")
        self.wait_turn_done()
        self.assertIn("without a code", self.llm_blob())

    def test_no_code_box_on_page(self):
        AskCodeTests.fill_ok = False
        self.start_code()
        a = self.wait_approval()
        self.eng.approvals.decide(a["id"], True, answer=CODE)
        self.wait_turn_done()
        blob = self.llm_blob()
        self.assertIn("no visible code box", blob)
        self.assertNotIn(CODE, blob)


if __name__ == "__main__":
    unittest.main()
