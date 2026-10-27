"""Plugin Connect routes: the callback that used to 500, and the Google return trip.

python -m unittest tests.test_plugin_connect_routes -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-connect-routes-"))

from fastapi.testclient import TestClient  # noqa: E402

from core.engine import Engine  # noqa: E402
from service.server import create_app  # noqa: E402

TOKEN = "t0ken"


class PluginConnectRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = Engine()
        cls.eng.start = lambda: None
        cls.eng.settings.set("service_token", TOKEN)
        # A server-wide Google app, i.e. "Continue with Google" is configured.
        cls.eng.settings.set("auth.google_client_id", "cid.apps.googleusercontent.com")
        cls.eng.settings.set("auth.google_client_secret", "csecret")
        cls.app = create_app(cls.eng, TOKEN)
        cls.h = {"Authorization": "Bearer " + TOKEN}

    def _start_connect(self, pid: str, mobile: int = 1) -> str:
        r = self.c.post(f"/api/plugins/{pid}/connect?mobile={mobile}", headers=self.h, json={})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["url"]

    def setUp(self):
        self.c = TestClient(self.app)

    def test_unknown_oauth_callback_is_not_a_crash(self):
        # Every non-Google Connect used to die here with a 500 (missing
        # PluginManager.pending), so vendors could never hand control back.
        r = self.c.get("/oauth/callback?code=x&state=nope")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Sign-in failed", r.text)

    def test_google_plugin_connect_uses_the_app_callback(self):
        url = self._start_connect("gmail")
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"), url)
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["redirect_uri"][0], "http://testserver/auth/google-app/cb")
        self.assertEqual(q["client_id"][0], "cid.apps.googleusercontent.com")
        self.assertIn("gmail.modify", q["scope"][0])
        self.assertEqual(q["access_type"][0], "offline")
        self.assertIn("code_challenge", q)

    def test_google_plugin_connect_needs_no_pasted_client(self):
        info = next(p for p in self.c.get("/api/plugins", headers=self.h).json()["plugins"] if p["id"] == "gmail")
        self.assertTrue(info["managed"])
        self.assertEqual(info["fields"], [])
        self.assertEqual(info["setup_url"], "")

    def test_google_callback_finishes_a_plugin_connect(self):
        url = self._start_connect("gdocs")
        state = parse_qs(urlparse(url).query)["state"][0]
        self.assertIn(state, self.eng.plugins.pending, "the callback can only work if the state is tracked")

        calls = {}

        def fake_finish(st: str, code: str) -> str:
            calls["args"] = (st, code)
            self.eng.plugins.pending.pop(st, None)
            return "gdocs"

        orig = self.eng.plugins.finish_oauth
        self.eng.plugins.finish_oauth = fake_finish
        try:
            r = self.c.get(f"/auth/google-app/cb?code=the-code&state={state}")
        finally:
            self.eng.plugins.finish_oauth = orig
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(calls.get("args"), (state, "the-code"))
        self.assertIn("Connected", r.text)
        # phone gets sent back into the app, desktop just gets a "close this tab" page
        self.assertIn("zerothebot://plugins/go?pid=gdocs", r.text)

    def test_google_callback_still_logs_in_the_app(self):
        # The app-login state is an HMAC, not a plugin state: it must not be
        # mistaken for a plugin Connect.
        r = self.c.get("/auth/google-app/cb?code=x&state=not-a-plugin-state")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Sign-in expired", r.text)


if __name__ == "__main__":
    unittest.main()
