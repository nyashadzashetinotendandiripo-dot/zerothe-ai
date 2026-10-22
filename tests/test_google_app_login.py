import unittest

from fastapi.testclient import TestClient

from core.engine import Engine
from service.server import create_app

TOKEN = "t0ken"


class GoogleAppFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = Engine()
        cls.eng.start = lambda: None
        cls.eng.settings.set("service_token", TOKEN)
        cls.app = create_app(cls.eng, TOKEN)

    def setUp(self):
        self.eng.settings.set("auth.google_client_id", "")
        self.eng.settings.set("auth.google_client_secret", "")
        self.c = TestClient(self.app, follow_redirects=False)

    def test_start_redirects_to_google_when_configured(self):
        self.eng.settings.set("auth.google_client_id", "1234.apps.googleusercontent.com")
        self.eng.settings.set("auth.google_client_secret", "s3cret")
        r = self.c.get("/auth/google-app/start", headers={"host": "8765-x.instabox.tech"})
        self.assertEqual(r.status_code, 307)
        self.assertIn("accounts.google.com/o/oauth2/v2/auth", r.headers["location"])
        self.assertIn("redirect_uri=https%3A%2F%2F8765-x.instabox.tech%2Fauth%2Fgoogle-app%2Fcb", r.headers["location"])
        self.assertIn("scope=openid+email", r.headers["location"])

    def test_start_not_set_up(self):
        r = self.c.get("/auth/google-app/start")
        self.assertEqual(r.status_code, 200)
        self.assertIn("not set up", r.text.lower())

    def test_start_uses_https_even_if_proxy_forgets_to_say_so(self):
        self.eng.settings.set("auth.google_client_id", "1234.apps.googleusercontent.com")
        self.eng.settings.set("auth.google_client_secret", "s3cret")
        r = self.c.get("/auth/google-app/start", headers={"host": "8765-x.instabox.tech", "x-forwarded-proto": "http"})
        self.assertEqual(r.status_code, 307)
        self.assertIn("redirect_uri=https%3A%2F%2F8765-x.instabox.tech", r.headers["location"])

    def test_api_google_requires_client_id(self):
        self.eng.settings.set("auth.google_client_id", "")
        r = self.c.post("/api/google", json={"id_token": "a.b.c"})
        self.assertEqual(r.status_code, 403)

    def test_callback_rejects_bad_state(self):
        self.eng.settings.set("auth.google_client_id", "1234.apps.googleusercontent.com")
        self.eng.settings.set("auth.google_client_secret", "s3cret")
        r = self.c.get("/auth/google-app/cb?code=abc&state=wrong")
        self.assertEqual(r.status_code, 200)
        self.assertIn("expired", r.text.lower())

    def test_callback_cancelled(self):
        r = self.c.get("/auth/google-app/cb?error=access_denied")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cancelled", r.text.lower())

    def test_no_secret_means_rejected(self):
        self.eng.settings.set("auth.google_client_id", "1234.apps.googleusercontent.com")
        r = self.c.get("/auth/google-app/cb?code=abc&state=whatever")
        self.assertEqual(r.status_code, 200)
        self.assertIn("expired", r.text.lower())


if __name__ == "__main__":
    unittest.main()
