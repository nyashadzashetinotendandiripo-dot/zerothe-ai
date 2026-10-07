"""In-chat login filling: approval card -> credential store -> page fill. The password never reaches the model.

Run: python -m unittest tests.test_loginfill -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["OPENGROKBOT_HOME"] = tempfile.mkdtemp(prefix="gbtest-")

from core import secrets as secret_store  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.providers import LLMResult, ToolCall  # noqa: E402
from test_engine import FakeProvider, wait_for  # noqa: E402

import core.agent as agent_mod  # noqa: E402

PW = "S3cret-Not-In-Logs!"


class LoginFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["OPENGROKBOT_HOME"] = tempfile.mkdtemp(prefix="gbtest-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        self.bot = self.eng.bots.create(f"Login Bot {uuid.uuid4().hex[:8]}")
        self.th = self.eng.threads.main_thread(self.bot["id"])
        self._saved: dict[str, str] = {}
        self._orig_get, self._orig_set = secret_store.get_secret, secret_store.set_secret
        secret_store.get_secret = lambda k: self._saved.get(k)
        secret_store.set_secret = lambda k, v: self._saved.__setitem__(k, v)

        class _Scr:
            def fill_credentials(self, *a, **k):
                return {"filled": True}

        # fake browser: no real page; call returns a successful fill regardless of the coroutine
        self.br = self.eng.computer.browser
        self._orig_screen, self._orig_call = self.br.screen, self.br.call
        self.br.screen = lambda bid: _Scr()
        self.br.call = lambda fn, *a, **k: {"filled": True, "username_filled": True}

    def tearDown(self):
        secret_store.get_secret, secret_store.set_secret = self._orig_get, self._orig_set
        self.br.screen, self.br.call = self._orig_screen, self._orig_call

    def use(self, script):
        fake = FakeProvider(script)
        agent_mod.make_provider = lambda *a, **k: fake
        import core.approvals as ap
        ap.make_provider = lambda *a, **k: fake
        return fake

    def start_login(self):
        self.use([
            LLMResult(tool_calls=[ToolCall("t1", "login_fill",
                                           {"domain": "portal.example.com", "purpose": "check my balance"})]),
            LLMResult(text="done."),
        ])
        self.eng.send_user_message(self.th["id"], "log in and check the balance")

    def wait_approval(self) -> dict:
        ok = wait_for(lambda: bool(self.eng.approvals.list("pending", self.bot["id"])))
        self.assertTrue(ok, "a login approval should be pending")
        return self.eng.approvals.list("pending", self.bot["id"])[0]

    def wait_turn_done(self):
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(self.bot["id"]), 30), "turn should finish")

    def tool_results(self) -> list[str]:
        return [r["content"] for r in self.eng.threads.rows(self.th["id"]) if r.get("kind") == "llm"]

    def test_approved_fill_and_no_password_leak(self):
        self.start_login()
        a = self.wait_approval()
        self.assertEqual(a["category"], "login")
        self.assertTrue(a["details"].get("fields"))
        self.assertEqual(a["details"].get("domain"), "portal.example.com")
        self.eng.approvals.decide(a["id"], True,
                                  answer=json.dumps({"username": "nyasha@example.com", "password": PW, "remember": True}))
        self.wait_turn_done()
        blob = "\n".join(self.tool_results())
        self.assertIn("Filled the login form for portal.example.com", blob)
        self.assertNotIn(PW, blob)
        for row in self.eng.actions.list(self.bot["id"]):
            self.assertNotIn(PW, json.dumps(row))
        self.assertIn("login:portal.example.com", self._saved)
        self.assertEqual(json.loads(self._saved["login:portal.example.com"])["password"], PW)

    def test_denied_fill_stops(self):
        self.start_login()
        a = self.wait_approval()
        self.eng.approvals.decide(a["id"], False)
        self.wait_turn_done()
        blob = "\n".join(self.tool_results())
        self.assertIn("declined", blob.lower())
        self.assertEqual(self._saved, {})

    def test_saved_credentials_fill_without_asking(self):
        self._saved["login:portal.example.com"] = json.dumps({"username": "nyasha@example.com", "password": PW})
        self.start_login()
        self.wait_turn_done()
        self.assertEqual(self.eng.approvals.list("pending", self.bot["id"]), [], "saved credentials must not ask again")
        blob = "\n".join(self.tool_results())
        self.assertIn("saved credential store", blob)
        self.assertNotIn(PW, blob)

    def test_empty_password_rejected(self):
        self.start_login()
        a = self.wait_approval()
        self.eng.approvals.decide(a["id"], True, answer=json.dumps({"username": "x", "password": ""}))
        self.wait_turn_done()
        blob = "\n".join(self.tool_results())
        self.assertIn("No password was provided", blob)


if __name__ == "__main__":
    unittest.main()
