"""Phone vertical slice: view chats, send a message, start a thread, approve — via TestClient, no browser."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-phone-")

from fastapi.testclient import TestClient  # noqa: E402

from core.engine import Engine  # noqa: E402
from service.server import create_app  # noqa: E402

TOKEN = "phone-slice-token"


class PhoneSlice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_engine import FakeProvider  # noqa: E402
        from core.providers import LLMResult  # noqa: E402
        import core.agent as agent_mod  # noqa: E402
        agent_mod.make_provider = lambda *a, **k: FakeProvider([LLMResult(text="ok")])
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()
        cls.c = TestClient(create_app(cls.eng, TOKEN))
        cls.h = {"Authorization": f"Bearer {TOKEN}"}
        cls.bot = cls.c.post("/api/bots", headers=cls.h, json={"name": "Phone Bot"}).json()
        cls.tid = cls.c.get(f"/api/bots/{cls.bot['id']}/threads", headers=cls.h).json()[0]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def test_view_send_approve(self):
        c, h = self.c, self.h
        b = c.get("/api/bootstrap", headers=h).json()  # view chats
        self.assertTrue(b["bots"] and "approvals" in b)
        ths = c.get(f"/api/bots/{self.bot['id']}/threads", headers=h).json()
        self.assertTrue(any(t["main"] for t in ths))
        self.assertIn("items", c.get(f"/api/threads/{self.tid}", headers=h).json())  # open thread
        r = c.post(f"/api/threads/{self.tid}/messages", headers=h, json={"text": "hello from phone"})
        self.assertIn(self.bot["id"], r.json()["started"])  # send
        users = [i for i in c.get(f"/api/threads/{self.tid}", headers=h).json()["items"] if i["type"] == "user"]
        self.assertIn("hello from phone", repr(users))
        n = c.post(f"/api/bots/{self.bot['id']}/threads", headers=h, json={"title": "Phone plan"}).json()  # start new
        self.assertTrue(n["id"])
        aid = "phone-appr-1"
        self.eng.db.insert("approvals", {"id": aid, "bot_id": self.bot["id"], "thread_id": self.tid, "category": "exec",
                                         "tool": "run", "summary": "Run deploy script", "details": "{}",
                                         "status": "pending", "created_at": time.time()})
        pend = c.get("/api/approvals", headers=h, params={"status": "pending"}).json()
        self.assertTrue(any(a["id"] == aid for a in pend))
        self.assertEqual(c.post(f"/api/approvals/{aid}/decide", headers=h, json={"approve": True}).json()["status"], "approved")
        self.assertIn("events", c.get("/api/poll", headers=h, params={"after": 0}).json())  # live updates
        m = c.get("/api/mobile", headers=h).json()  # pairing info
        self.assertTrue(m["urls"] and m["token"] == TOKEN)
        self.assertGreaterEqual(len(c.get("/api/commands", headers=h).json()), 5)
        for p in ("/", "/app.js", "/manifest.json", "/sw.js"):  # PWA shell
            self.assertEqual(c.get(p).status_code, 200, p)
        self.assertEqual(c.get("/api/health").status_code, 200)
        self.assertEqual(c.get("/api/bots").status_code, 401)


if __name__ == "__main__":
    unittest.main()
