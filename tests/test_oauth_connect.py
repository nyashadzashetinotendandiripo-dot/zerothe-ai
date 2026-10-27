"""Generic OAuth Connect (X / QuickBooks / Xero / Sage) + Chrome browser channel.
python -m unittest tests.test_oauth_connect -v"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-oauth-"))

from core.plugins import ConnectorError, GoogleOAuth, PluginManager, builtin_defs  # noqa: E402


def fake_mgr(defs, cfgs=None, toks=None):
    toks = toks or {}
    import unittest.mock as mock
    mgr = SimpleNamespace(defs={d.id: d for d in defs},
                          settings=SimpleNamespace(get=lambda k, d=None: (cfgs or {}).get(k, {})),
                          google=GoogleOAuth())
    mgr.config_value = types.MethodType(PluginManager.config_value, mgr)
    mgr.configured = types.MethodType(PluginManager.configured, mgr)
    mgr.begin_oauth = types.MethodType(PluginManager.begin_oauth, mgr)
    mgr.finish_oauth = types.MethodType(PluginManager.finish_oauth, mgr)
    patcher = mock.patch("core.secrets.get_secret", lambda name: toks.get(name))
    patcher.start()
    return mgr, patcher


def google_defs():
    return [d for d in builtin_defs() if d.id in ("gmail", "google_calendar", "gdocs")]


class OAuthUrlTests(unittest.TestCase):
    def test_x_connect_url(self):
        from core.plugins import PluginDef
        xd = PluginDef("x", "X", "d", kind="python",
                       fields=[{"key": "token", "secret": True, "required": False},
                               {"key": "client_id", "secret": False, "required": False}],
                       oauth={"provider": "x", "scopes": ["tweet.read", "tweet.write", "users.read", "offline.access"],
                              "auth_url": "https://x.com/i/oauth2/authorize",
                              "token_url": "https://api.x.com/2/oauth2/token", "secret_style": "basic"})
        mgr, p = fake_mgr([xd], {"plugin_cfg.x": {"client_id": "cid123"}})
        try:
            url = mgr.begin_oauth("x", "http://127.0.0.1:8765/oauth/callback")
        finally:
            p.stop()
        self.assertTrue(url.startswith("https://x.com/i/oauth2/authorize?"), url)
        self.assertIn("scope=tweet.read", url)
        self.assertIn("code_challenge=", url)
        state = [s for s in mgr.google.pending][0]
        pend = mgr.google.pending[state]
        self.assertEqual(pend["token_url"], "https://api.x.com/2/oauth2/token")
        self.assertEqual(pend["secret_style"], "basic")

    def test_connect_needs_client_id(self):
        from core.plugins import PluginDef
        xd = PluginDef("x", "X", "d", kind="python", fields=[],
                       oauth={"provider": "x", "scopes": ["tweet.read"], "auth_url": "https://x.com/i/oauth2/authorize",
                              "token_url": "https://api.x.com/2/oauth2/token", "secret_style": "basic"})
        mgr, p = fake_mgr([xd], {})
        try:
            with self.assertRaises(ConnectorError):
                mgr.begin_oauth("x", "http://127.0.0.1:8765/oauth/callback")
        finally:
            p.stop()

    def test_gmail_fallback_untouched_and_scoped(self):
        mgr, p = fake_mgr(google_defs(), {"plugin_cfg.gmail": {"client_id": "gmail-cid"}})
        try:
            import unittest.mock as mock
            # Google needs the client secret to exchange the code, so the
            # fallback client is only usable with one.
            with mock.patch("core.secrets.get_secret",
                            lambda n: "gmail-csec" if n == "plugin:gmail:client_secret" else None):
                url = mgr.begin_oauth("gdocs", "http://127.0.0.1:8765/oauth/callback")
            self.assertIn("accounts.google.com", url)
            self.assertIn("client_id=gmail-cid", url)
        finally:
            p.stop()

    def test_configured_x_either_or(self):
        from core.plugins import PluginDef
        xd = PluginDef("x", "X", "d", kind="python",
                       fields=[{"key": "token", "secret": True, "required": True},
                               {"key": "client_id", "secret": False, "required": False}],
                       oauth={"provider": "x", "scopes": []})
        mgr, p = fake_mgr([xd], {"plugin_cfg.x": {"client_id": "cid"}})
        try:
            import unittest.mock as mock
            with mock.patch("core.secrets.get_secret", lambda n: {"plugin:x:refresh_token": "rt"}.get(n)):
                self.assertTrue(mgr.configured("x"))
            with mock.patch("core.secrets.get_secret", lambda n: None):
                self.assertFalse(mgr.configured("x"))
        finally:
            p.stop()
        mgr2, p2 = fake_mgr([xd], {})
        try:
            import unittest.mock as mock
            with mock.patch("core.secrets.get_secret", lambda n: {"plugin:x:token": "bearer"}.get(n)):
                self.assertTrue(mgr2.configured("x"))
        finally:
            p2.stop()


class OAuthExchangeTests(unittest.TestCase):
    def setUp(self):
        outer = self
        self.authz = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n).decode()
                outer.authz.append(dict(self.headers))
                outer.last_body = body
                if "code=bad" in body:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error": "invalid_grant"}')
                    return
                raw = json.dumps({"access_token": "acc", "refresh_token": "ref2", "expires_in": 3600}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.token_url = f"http://127.0.0.1:{self.srv.server_address[1]}/oauth2/token"
        import core.plugins as plugmod
        plugmod._OAUTH2_CACHE.pop("qx", None)
        self.addCleanup(self.srv.shutdown)

    def test_basic_style_sends_header(self):
        g = GoogleOAuth()
        url = g.auth_url("qx", "cid", ["scope.a"], "http://x/cb", auth_url="http://x/auth",
                         token_url=self.token_url, secret_style="basic")
        self.assertIn("http://x/auth?", url)
        state = url.split("state=")[1].split("&")[0]
        import unittest.mock as mock
        saved = {}
        with mock.patch("core.secrets.set_secret", lambda n, v: saved.__setitem__(n, v)):
            who = g.exchange(state, "code123", "cid", "csec", token_url=self.token_url, secret_style="basic")
        self.assertEqual(who, "qx")
        self.assertTrue(self.authz[-1].get("Authorization", "").startswith("Basic "), self.authz[-1])
        self.assertNotIn("client_secret=csec", self.last_body)
        self.assertEqual(saved.get("plugin:qx:refresh_token"), "ref2")

    def test_body_style_default(self):
        g = GoogleOAuth()
        url = g.auth_url("qy", "cid", ["s"], "http://x/cb", auth_url="http://x/auth", token_url=self.token_url)
        state = url.split("state=")[1].split("&")[0]
        import unittest.mock as mock
        with mock.patch("core.secrets.set_secret", lambda n, v: None):
            g.exchange(state, "code123", "cid", "csec", token_url=self.token_url)
        self.assertNotIn("Authorization", self.authz[-1])
        self.assertIn("client_secret=csec", self.last_body)

    def test_finish_oauth_uses_plugin_token_url(self):
        from core.plugins import PluginDef
        qd = PluginDef("quickbooks", "QB", "d", kind="python",
                       fields=[{"key": "client_id", "secret": False, "required": True},
                               {"key": "client_secret", "secret": True, "required": True}],
                       oauth={"provider": "intuit", "scopes": ["com.intuit.quickbooks.accounting"],
                              "auth_url": "https://appcenter.intuit.com/connect/oauth2",
                              "token_url": self.token_url, "secret_style": "basic"})
        mgr, p = fake_mgr([qd], {"plugin_cfg.quickbooks": {"client_id": "cid"}})
        try:
            import unittest.mock as mock
            url = mgr.begin_oauth("quickbooks", "http://127.0.0.1:8765/oauth/callback")
            self.assertIn("appcenter.intuit.com", url)
            state = url.split("state=")[1].split("&")[0]
            saved = {}
            with mock.patch("core.secrets.set_secret", lambda n, v: saved.__setitem__(n, v)):
                who = mgr.finish_oauth(state, "code123")
            self.assertEqual(who, "quickbooks")
            self.assertEqual(saved.get("plugin:quickbooks:refresh_token"), "ref2")
        finally:
            p.stop()


class VendorRefreshTests(unittest.TestCase):
    def setUp(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                outer.authz = dict(self.headers)
                raw = json.dumps({"access_token": "acc", "expires_in": 3600}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/tokens/bearer"

    def tearDown(self):
        self.srv.shutdown()

    def _plugin_token(self, pid, payload_extra=None):
        import core.plugins as plugmod
        import unittest.mock as mock
        plugmod._OAUTH2_CACHE.pop("qbtest", None)
        with mock.patch("core.secrets.set_secret", lambda n, v: None):
            return plugmod.oauth2_token(None, "qbtest", self.url,
                                        {"grant_type": "refresh_token", **(payload_extra or {})},
                                        headers={"Authorization": "Basic Y2lkOmNzZWM="})

    def test_basic_header_passes_through(self):
        d = self._plugin_token("x")
        self.assertEqual(d["access_token"], "acc")
        self.assertEqual(self.authz.get("Authorization"), "Basic Y2lkOmNzZWM=")


class SetupLinkTests(unittest.TestCase):
    bundled = ["asana", "hubspot", "x", "zoom", "salesforce", "quickbooks",
               "xero", "sage", "teams", "onshape", "fusion"]

    def test_bundled_oauth_plugins_have_setup_links(self):
        for pid in self.bundled:
            m = json.loads((Path(__file__).resolve().parent.parent / "plugins" / pid / "plugin.json").read_text(encoding="utf-8"))
            self.assertTrue(m.get("oauth"), f"{pid} should offer browser Connect")
            self.assertTrue((m.get("setup_url") or "").startswith("https://"), f"{pid} should link its vendor setup page")
            self.assertTrue(m.get("setup_label"), f"{pid} should label its setup link")
            self.assertIn("Connect", m.get("help", ""), f"{pid} help should point at browser Connect")

    def test_token_plugins_have_setup_links(self):
        from core.plugins import builtin_defs
        for pid in ("gmail", "slack", "notion", "linear", "jira", "github"):
            d = next(x for x in builtin_defs() if x.id == pid)
            self.assertTrue((d.setup_url or "").startswith("https://"), f"{pid} should link its vendor setup page")
            self.assertTrue(d.setup_label, f"{pid} should label its setup link")

    def test_hubspot_uses_versioned_token_endpoint(self):
        m = json.loads((Path(__file__).resolve().parent.parent / "plugins" / "hubspot" / "plugin.json").read_text(encoding="utf-8"))
        self.assertIn("/oauth/2026-03/token", (m.get("oauth") or {}).get("token_url", ""),
                      "HubSpot v1 token endpoint sunsets Feb 2027; use the versioned one")
        self.assertNotIn("/oauth/v1/token", (m.get("oauth") or {}).get("token_url", ""))
        self.assertIn("Service Key", m.get("help", ""))

    def test_begin_oauth_per_plugin_hosts(self):
        from core.plugins import PluginDef
        sales = PluginDef("salesforce", "SF", "d", kind="python",
                          fields=[{"key": "client_id", "secret": False, "required": True},
                                  {"key": "client_secret", "secret": True, "required": True}],
                          oauth={"provider": "salesforce", "scopes": ["api", "refresh_token"],
                                 "auth_url": "https://login.salesforce.com/services/oauth2/authorize",
                                 "token_url": "https://login.salesforce.com/services/oauth2/token",
                                 "secret_style": "body"})
        mgr, p = fake_mgr([sales], {"plugin_cfg.salesforce": {"client_id": "cid", "login_url": "https://test.salesforce.com"}})
        try:
            url = mgr.begin_oauth("salesforce", "http://127.0.0.1:8765/oauth/callback")
        finally:
            p.stop()
        self.assertIn("test.salesforce.com/services/oauth2/authorize", url)

    def test_configured_either_or_paths(self):
        import unittest.mock as mock
        from core.plugins import PluginDef
        asana = PluginDef("asana", "Asana", "d", kind="declarative",
                          fields=[{"key": "token", "secret": True, "required": False},
                                  {"key": "client_id", "secret": False, "required": False}],
                          oauth={"provider": "asana", "scopes": ["default"]})
        mgr, p = fake_mgr([asana], {})
        try:
            with mock.patch("core.secrets.get_secret", lambda n: {"plugin:asana:token": "pat"}.get(n)):
                self.assertTrue(mgr.configured("asana"), "pasted PAT counts without Connect")
            with mock.patch("core.secrets.get_secret", lambda n: None):
                self.assertFalse(mgr.configured("asana"), "nothing pasted and no Connect")
        finally:
            p.stop()
        zoom = PluginDef("zoom", "Zoom", "d", kind="python",
                         fields=[{"key": "client_id", "secret": False, "required": True},
                                 {"key": "client_secret", "secret": True, "required": True},
                                 {"key": "account_id", "secret": False, "required": False}],
                         oauth={"provider": "zoom", "scopes": ["meeting:read"]})
        mgr2, p2 = fake_mgr([zoom], {"plugin_cfg.zoom": {"client_id": "c", "account_id": "a"}},
                                {"plugin:zoom:client_secret": "s"})
        try:
            with mock.patch("core.secrets.get_secret",
                            lambda n: {"plugin:zoom:client_secret": "s"}.get(n)):
                self.assertTrue(mgr2.configured("zoom"), "Server-to-Server path needs no Connect")
        finally:
            p2.stop()

    def test_declarative_token_prefers_oauth(self):
        import unittest.mock as mock
        import core.plugins as plugmod
        from core.plugins import Conn, PluginDef
        d = PluginDef("asana", "Asana", "x", kind="declarative",
                      fields=[{"key": "token", "secret": True, "required": False}],
                      oauth={"provider": "asana", "scopes": ["default"],
                             "auth_url": "https://app.asana.com/-/oauth_authorize",
                             "token_url": "https://app.asana.com/-/oauth_token", "secret_style": "body"})
        mgr, p = fake_mgr([d], {"plugin_cfg.asana": {"client_id": "cid", "client_secret": "csec"}})
        plugmod._OAUTH2_CACHE.pop("asana", None)
        try:
            mgr.defs["asana"] = d
            conn = Conn(mgr, d)
            with mock.patch("core.secrets.get_secret",
                            lambda n: {"plugin:asana:refresh_token": "rt", "plugin:asana:token": "old-pat"}.get(n)):
                with mock.patch.object(plugmod, "oauth2_token", lambda *a, **k: {"access_token": "fresh"}):
                    self.assertEqual(conn.cfg("token"), "fresh", "Connect token wins over a pasted PAT")
            with mock.patch("core.secrets.get_secret", lambda n: {"plugin:asana:token": "old-pat"}.get(n)):
                self.assertEqual(conn.cfg("token"), "old-pat", "PAT still works with no Connect")
        finally:
            p.stop()
            plugmod._OAUTH2_CACHE.pop("asana", None)


class BrowserChannelTests(unittest.TestCase):
    def _host(self, computer):
        from core.browser import BrowserHost
        settings = SimpleNamespace(get=lambda k, d=None: {"computer": computer}.get(k, d))
        return BrowserHost(settings, None, None)

    def _fake_pw(self, fail_first=()):
        calls = []

        class FakePage:
            closed = False

            def is_closed(self):
                return False

        class FakeCtx:
            pages = []

            async def new_page(self):
                return FakePage()

            def on(self, *a):
                pass

            async def expose_binding(self, *a):
                pass

            async def add_init_script(self, *a):
                pass

        class FakeChromium:
            async def launch_persistent_context(self, profile, **kw):
                calls.append(kw)
                for marker in fail_first:
                    if marker in str(kw):
                        raise Exception("Executable doesn't exist at fake path (chrome)")
                return FakeCtx()

        class FakePW:
            chromium = FakeChromium()

        return FakePW(), calls

    def test_chromium_channel_explicit(self):
        import asyncio
        host = self._host({"headless": True})
        pw, calls = self._fake_pw()
        host._pw = pw
        host.call(host._context)
        self.assertEqual(calls[-1].get("channel"), "chromium")

    def test_chrome_channel_and_fallback(self):
        host = self._host({"headless": True, "browser": "chrome"})
        pw, calls = self._fake_pw(fail_first=("'channel': 'chrome'",))
        host._pw = pw
        host.call(host._context)
        self.assertEqual(calls[0].get("channel"), "chrome")
        self.assertNotIn("channel", calls[-1], "falls back to bundled build without channel")
        self.assertIn("bundled Chromium", host.last_error)

    def test_unknown_value_falls_back_to_chromium(self):
        host = self._host({"browser": "firefox"})
        pw, calls = self._fake_pw()
        host._pw = pw
        host.call(host._context)
        self.assertEqual(calls[-1].get("channel"), "chromium")


if __name__ == "__main__":
    unittest.main()
