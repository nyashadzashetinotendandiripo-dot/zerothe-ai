"""Phone-app fixes: reusable login session + real reply threading."""

import unittest

from fastapi.testclient import TestClient

from core.engine import Engine
from service.server import create_app

TOKEN = "t0ken"


class MobileFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = Engine()
        cls.eng.start = lambda: None
        cls.eng.settings.set("service_token", TOKEN)
        cls.app = create_app(cls.eng, TOKEN)

    def test_login_returns_a_reusable_session_token(self):
        c = TestClient(self.app)
        r = c.post("/api/login", json={"email": "owner@example.com", "token": TOKEN})
        self.assertEqual(r.status_code, 200, r.text)
        sess = r.json().get("session") or ""
        self.assertIn(".", sess)
        # the app stores this and sends it as a Bearer token instead of a password
        r2 = c.get("/api/bots", headers={"Authorization": "Bearer " + sess})
        self.assertEqual(r2.status_code, 200, r2.text)

    def test_reply_parent_id_is_persisted(self):
        bot = self.eng.bots.create("ReplyBot", job="reply test")
        th = self.eng.threads.main_thread(bot["id"])
        self.eng.send_user_message(th["id"], "first message")
        users = [i for i in self.eng.threads.display(th["id"]) if i.get("type") == "user"]
        self.assertTrue(users)
        first = users[0]
        self.eng.send_user_message(th["id"], "second message", parent_id=first["id"])
        users = [i for i in self.eng.threads.display(th["id"]) if i.get("type") == "user"]
        second = users[-1]
        self.assertEqual(second["parent_id"], first["id"])


if __name__ == "__main__":
    unittest.main()
