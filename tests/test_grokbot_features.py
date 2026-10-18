"""Grok Bot parity features: pinned/hidden Bots, reactions, replies, palette, rules, computer reset, account delete."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-parity-"))

from fastapi.testclient import TestClient  # noqa: E402

from core import auth  # noqa: E402
from core.engine import Engine  # noqa: E402
from service.server import create_app  # noqa: E402

TOKEN = "parity-token-123"


class GrokBotParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-parity-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()
        cls.c = TestClient(create_app(cls.eng, TOKEN))
        cls.h = {"Authorization": f"Bearer {TOKEN}"}

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def _bot(self, **kw):
        body = {"name": kw.pop("name", "Parity Bot")}
        body.update(kw)
        r = self.c.post("/api/bots", headers=self.h, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_label_pinned_hidden_roundtrip_and_order(self):
        a = self._bot(name="Alpha")
        b = self._bot(name="Beta")
        r = self.c.put(f"/api/bots/{b['id']}", headers=self.h, json={"pinned": True, "hidden": True, "label": "  favourite  "})
        self.assertEqual(r.status_code, 200, r.text)
        got = self.c.get(f"/api/bots/{b['id']}", headers=self.h).json()
        self.assertTrue(got["pinned"])
        self.assertTrue(got["hidden"])
        self.assertEqual(got["label"], "favourite")
        listed = self.c.get("/api/bots", headers=self.h).json()
        ids = [x["id"] for x in listed if x["id"] in (a["id"], b["id"])]
        self.assertEqual(ids[0], b["id"], "pinned Bot should list first")

    def test_reactions_toggle_and_unreact(self):
        bot = self._bot(name="React Bot")
        tid = self.eng.threads.main_thread(bot["id"])["id"]
        mid = self.eng.threads.add(tid, "user", "user", "hello there")
        r = self.c.post(f"/api/messages/{mid}/reactions", headers=self.h, json={"emoji": "👍"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["on"])
        self.assertEqual(r.json()["reactions"][0]["count"], 1)
        # toggle again turns it off
        r = self.c.post(f"/api/messages/{mid}/reactions", headers=self.h, json={"emoji": "👍"})
        self.assertFalse(r.json()["on"])
        # add then remove explicitly
        self.c.post(f"/api/messages/{mid}/reactions", headers=self.h, json={"emoji": "🎉"})
        r = self.c.delete(f"/api/messages/{mid}/reactions", headers=self.h, params={"emoji": "🎉"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["reactions"], [])

    def test_reply_parent_id_is_kept(self):
        bot = self._bot(name="Reply Bot")
        tid = self.eng.threads.main_thread(bot["id"])["id"]
        parent = self.eng.threads.add(tid, "user", "user", "original")
        self.eng.threads.add(tid, "user", "user", "answering", parent_id=parent)
        items = self.eng.threads.display(tid)
        replies = [i for i in items if i.get("parent_id") == parent]
        self.assertTrue(replies, "reply should carry its parent_id")

    def test_palette_searches_across_types(self):
        bot = self._bot(name="Palette Finder", job="finds things")
        self.eng.threads.add(self.eng.threads.main_thread(bot["id"])["id"], "user", "user", "needle in a haystack")
        d = self.c.get("/api/palette", headers=self.h, params={"q": "Palette"}).json()
        self.assertTrue(any(b["id"] == bot["id"] for b in d["bots"]))
        d2 = self.c.get("/api/palette", headers=self.h, params={"q": "needle"}).json()
        self.assertTrue(d2["messages"])
        self.assertTrue(self.c.get("/api/palette", headers=self.h, params={"q": ""}).json()["total"] == 0)

    def test_event_triggers_crud_and_fire(self):
        import time
        import types

        bot = self._bot(name="Trigger Bot")
        r = self.c.post("/api/triggers", headers=self.h,
                        json={"bot_id": bot["id"], "name": "GitHub issue", "prompt": "Triage the issue."})
        self.assertEqual(r.status_code, 200, r.text)
        t = r.json()
        self.assertTrue(t["token"])
        self.assertIn("/api/hooks/", t["url"])
        self.assertEqual(t["bot_name"], "Trigger Bot")
        self.assertEqual(len(self.c.get("/api/triggers", headers=self.h, params={"bot_id": bot["id"]}).json()), 1)

        calls = []

        def fake_run_sync(bot_id, thread_id, task, trigger="routine", dry_run=False):
            calls.append({"bot_id": bot_id, "task": task, "trigger": trigger})
            return types.SimpleNamespace(turn_id="t1", final_text="done", status="done", error="")

        orig = self.eng.turns.run_sync
        self.eng.turns.run_sync = fake_run_sync
        try:
            h = self.c.post("/api/hooks/" + t["token"], json={"title": "Bug: crash", "number": 42})
            self.assertEqual(h.status_code, 200, h.text)
            self.assertTrue(h.json()["ok"])
            deadline = time.time() + 5
            while not calls and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(calls, "webhook should have started a turn")
            self.assertEqual(calls[0]["trigger"], "event")
            self.assertEqual(calls[0]["bot_id"], bot["id"])
            self.assertIn("Bug: crash", calls[0]["task"])
        finally:
            self.eng.turns.run_sync = orig

        self.assertEqual(self.c.post("/api/hooks/does-not-exist", json={}).status_code, 400)
        rot = self.c.post(f"/api/triggers/{t['id']}/rotate", headers=self.h)
        self.assertEqual(rot.status_code, 200, rot.text)
        self.assertNotEqual(rot.json()["token"], t["token"])
        self.assertEqual(self.c.delete(f"/api/triggers/{t['id']}", headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/api/triggers", headers=self.h, params={"bot_id": bot["id"]}).json(), [])

    def test_approval_rules_crud(self):
        bot = self._bot(name="Rules Bot")
        r = self.c.post("/api/rules", headers=self.h, json={"bot_id": bot["id"], "category": "shell", "pattern": "*"})
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["id"]
        got = self.c.get("/api/rules", headers=self.h, params={"bot_id": bot["id"]}).json()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["category"], "shell")
        self.assertEqual(self.c.post("/api/rules", headers=self.h, json={"bot_id": "", "category": "x"}).status_code, 400)
        self.assertEqual(self.c.delete(f"/api/rules/{rid}", headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/api/rules", headers=self.h, params={"bot_id": bot["id"]}).json(), [])

    def test_computer_reset_endpoints_do_not_500(self):
        for path in ("/api/computer/restart", "/api/computer/reset"):
            r = self.c.post(path, headers=self.h, json={})
            self.assertIn(r.status_code, (200, 400), r.text)

    def test_account_delete_requires_matching_email(self):
        self.c.post("/api/login", json={"email": "owner@parity.dev", "password": "test pass 123"})
        self.assertTrue(self.c.get("/api/auth_status").json()["needs_setup"])
        r = self.c.post("/api/signup", json={"email": "owner@parity.dev", "password": "test pass 123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(auth.has_account() is False)
        bad = self.c.post("/api/account/delete", headers=self.h, json={"confirm": "nope@no.dev"})
        self.assertEqual(bad.status_code, 400)
        ok = self.c.post("/api/account/delete", headers=self.h, json={"confirm": "owner@parity.dev"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(self.c.get("/api/auth_status").json()["needs_setup"])


if __name__ == "__main__":
    unittest.main()
