"""Google sign-in: setup page, session bearer, owner binding.  python -m unittest tests.test_google_login"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-google-")

from fastapi.testclient import TestClient  # noqa: E402

from core.engine import Engine  # noqa: E402
from core import auth as auth_mod  # noqa: E402
from service.server import create_app  # noqa: E402

TOKEN = "google-test-token"


class GoogleLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-google-cls-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()
        cls.app = create_app(cls.eng, TOKEN)
        cls.c = TestClient(cls.app)
        cls.h = {"Authorization": f"Bearer {TOKEN}"}

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        # Fresh data dir per test: recreate the app + client.
        self.app = create_app(self.eng, TOKEN)
        self.c = TestClient(self.app)

    def test_google_page_not_set_up(self):
        r = self.c.get("/auth/google-login")
        self.assertEqual(r.status_code, 200)
        self.assertIn("not set up", r.text.lower())

    def test_google_page_renders_client_id(self):
        self.eng.settings.set("auth.google_client_id", "1234.apps.googleusercontent.com")
        r = self.c.get("/auth/google-login")
        self.assertEqual(r.status_code, 200)
        self.assertIn("1234.apps.googleusercontent.com", r.text)
        self.assertIn("/auth/google-done?tok=", r.text)
        self.assertEqual(self.c.get("/auth/google-done").status_code, 200)

    def test_api_google_gates(self):
        # No account yet -> 403
        r = self.c.post("/api/google", json={"id_token": "nope"})
        self.assertEqual(r.status_code, 403)
        # Create account, then a malformed id_token -> 401
        self.c.post("/api/signup", json={"email": "owner@test.dev", "password": "test pass 123"})
        r = self.c.post("/api/google", json={"id_token": "not-a-token"})
        self.assertEqual(r.status_code, 401)

    def test_google_binding_and_status(self):
        r = self.c.get("/api/auth_status").json()
        self.assertFalse(r["google_ready"])
        auth_mod.set_google_email("owner@gmail.com")
        self.assertEqual(auth_mod.google_email(), "owner@gmail.com")
        r = self.c.get("/api/auth_status").json()
        self.assertTrue(r["google_ready"])

    def test_session_string_works_as_bearer(self):
        # Account with this email already exists (created in test_api_google_gates).
        r = self.c.post("/api/login", json={"email": "owner@test.dev", "password": "test pass 123"})
        self.assertIn("zt_session", r.cookies)
        sess = r.cookies["zt_session"]
        self.assertIn(".", sess)
        # Bearer session accepted even with no cookies at all
        alice = TestClient(self.app)
        self.assertEqual(alice.get("/api/bots", headers={"Authorization": "Bearer " + sess}).status_code, 200)
        # Garbage session rejected
        bad = sess.split(".", 1)[0] + ".deadbeef"
        self.assertEqual(alice.get("/api/bots", headers={"Authorization": "Bearer " + bad}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
