"""Google plugin Connect: server-wide Google client, managed fields, secret vault.

These cover the three bugs that made "Connect" look dead on a headless server:
no writable credential store, plugin Connect asking for its own OAuth client,
and the plugin callback 500ing because PluginManager had no `pending`.
python -m unittest tests.test_plugin_google_connect -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-gconnect-"))

from core import secrets  # noqa: E402
from core.plugins import GoogleOAuth, PluginDef, PluginManager, builtin_defs, google_client  # noqa: E402

SERVER_CID = "512722134597-83725pi2rqmm9bsm91fso1kmrnr5hs65.apps.googleusercontent.com"
SERVER_CSEC = "server-secret"


def google_defs():
    return [d for d in builtin_defs() if d.id in ("gmail", "google_calendar", "gdocs")]


def fake_mgr(defs, cfgs=None, toks=None):
    mgr = SimpleNamespace(defs={d.id: d for d in defs},
                          settings=SimpleNamespace(get=lambda k, d=None: (cfgs or {}).get(k, d)),
                          google=GoogleOAuth(), admin=SimpleNamespace())
    mgr.config_value = types.MethodType(PluginManager.config_value, mgr)
    mgr.configured = types.MethodType(PluginManager.configured, mgr)
    mgr.info = types.MethodType(PluginManager.info, mgr)
    mgr.begin_oauth = types.MethodType(PluginManager.begin_oauth, mgr)
    mgr.finish_oauth = types.MethodType(PluginManager.finish_oauth, mgr)
    mgr.list = types.MethodType(PluginManager.list, mgr)
    return mgr, (toks or {})


class VaultTests(unittest.TestCase):
    """A host with no OS keyring must still be able to store credentials."""

    def test_round_trip_encrypted_on_disk(self):
        from core.secrets import VAULT_FILE, _VaultBackend

        v = _VaultBackend()
        v.set_password(secrets.SERVICE, "plugin:gmail:refresh_token", "rt-plaintext-canary")
        self.assertEqual(v.get_password(secrets.SERVICE, "plugin:gmail:refresh_token"), "rt-plaintext-canary")

        blob = (Path(os.environ["ZEROTHEBOT_HOME"]) / VAULT_FILE).read_bytes()
        self.assertTrue(blob)
        self.assertNotIn(b"rt-plaintext-canary", blob, "the vault must not store secrets in the clear")

        v.delete_password(secrets.SERVICE, "plugin:gmail:refresh_token")
        self.assertIsNone(v.get_password(secrets.SERVICE, "plugin:gmail:refresh_token"))

    def test_missing_secret_reads_as_none(self):
        from core.secrets import _VaultBackend

        self.assertIsNone(_VaultBackend().get_password(secrets.SERVICE, "plugin:nope:nothing"))

    def test_set_secret_uses_vault_when_os_store_is_missing(self):
        from core.secrets import _VaultBackend

        vault = _VaultBackend()
        with mock.patch("core.secrets.backend_available", lambda: True), \
                mock.patch("core.secrets.keyring") as kr:
            kr.set_password = vault.set_password
            kr.get_password = vault.get_password
            kr.delete_password = vault.delete_password
            secrets.set_secret("plugin:slack:bot_token", "xoxb-test")
            self.assertEqual(secrets.get_secret("plugin:slack:bot_token"), "xoxb-test")
            secrets.delete_secret("plugin:slack:bot_token")
            self.assertIsNone(secrets.get_secret("plugin:slack:bot_token"))


class GoogleClientTests(unittest.TestCase):
    def test_server_google_app_wins_over_pasted_client(self):
        mgr, _ = fake_mgr(google_defs(), {
            "auth": {"google_client_id": SERVER_CID, "google_client_secret": SERVER_CSEC},
            "plugin_cfg.gmail": {"client_id": "pasted.apps.googleusercontent.com"},
        })
        self.assertEqual(google_client(mgr, "gmail"), (SERVER_CID, SERVER_CSEC))
        self.assertEqual(google_client(mgr, "gdocs"), (SERVER_CID, SERVER_CSEC))

    def test_falls_back_to_gmail_client_then_own(self):
        mgr, _ = fake_mgr(google_defs(), {"plugin_cfg.gmail": {"client_id": "g.apps.googleusercontent.com"}})
        with mock.patch("core.secrets.get_secret", lambda n: "gsec" if n == "plugin:gmail:client_secret" else None):
            self.assertEqual(google_client(mgr, "google_calendar"), ("g.apps.googleusercontent.com", "gsec"))
        mgr, _ = fake_mgr(google_defs(), {"plugin_cfg.gdocs": {"client_id": "own.apps.googleusercontent.com"}})
        with mock.patch("core.secrets.get_secret", lambda n: "ownsec" if n == "plugin:gdocs:client_secret" else None):
            self.assertEqual(google_client(mgr, "gdocs"), ("own.apps.googleusercontent.com", "ownsec"))

    def test_non_google_plugin_has_no_google_client(self):
        xd = PluginDef("x", "X", "d", fields=[{"key": "client_id", "secret": False}],
                       oauth={"provider": "x", "scopes": ["tweet.read"]})
        mgr, _ = fake_mgr([xd], {"auth": {"google_client_id": SERVER_CID, "google_client_secret": SERVER_CSEC}})
        self.assertEqual(google_client(mgr, "x"), ("", ""))


class ManagedInfoTests(unittest.TestCase):
    def test_google_plugins_hide_manual_setup(self):
        mgr, _ = fake_mgr(google_defs(), {
            "auth": {"google_client_id": SERVER_CID, "google_client_secret": SERVER_CSEC},
        })
        with mock.patch("core.plugins.plugin_allowed", lambda admin, pid: True), \
                mock.patch("core.secrets.get_secret", lambda n: None):
            info = mgr.info("gmail")
        self.assertTrue(info["managed"])
        self.assertEqual(info["fields"], [])
        self.assertEqual(info["setup_url"], "")
        self.assertIn("One tap", info["help"])
        self.assertFalse(info["connected"])
        self.assertFalse(info["configured"], "still needs the Connect round-trip")

    def test_plugin_without_server_client_keeps_fields(self):
        mgr, _ = fake_mgr(google_defs(), {})
        with mock.patch("core.plugins.plugin_allowed", lambda admin, pid: True), \
                mock.patch("core.secrets.get_secret", lambda n: None):
            info = mgr.info("gmail")
        self.assertFalse(info["managed"])
        self.assertEqual([f["key"] for f in info["fields"]], ["client_id", "client_secret"])
        self.assertTrue(info["setup_url"].startswith("https://"))

    def test_configured_once_signed_in(self):
        mgr, _ = fake_mgr(google_defs(), {
            "auth": {"google_client_id": SERVER_CID, "google_client_secret": SERVER_CSEC},
        })
        with mock.patch("core.secrets.get_secret", lambda n: None):
            self.assertFalse(mgr.configured("gmail"))
        with mock.patch("core.secrets.get_secret", lambda n: "rt" if n == "plugin:gmail:refresh_token" else None):
            self.assertTrue(mgr.configured("gmail"))


class BeginOAuthTests(unittest.TestCase):
    def test_google_connect_url_uses_server_client_and_pkce(self):
        mgr, _ = fake_mgr(google_defs(), {
            "auth": {"google_client_id": SERVER_CID, "google_client_secret": SERVER_CSEC},
        })
        url = mgr.begin_oauth("gmail", "https://example.test/auth/google-app/cb", mobile=True)
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"), url)
        self.assertIn("client_id=" + SERVER_CID, url)
        self.assertIn("redirect_uri=https%3A%2F%2Fexample.test%2Fauth%2Fgoogle-app%2Fcb", url)
        self.assertIn("code_challenge=", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("gmail.modify", url.replace("%2F", "/"))
        state = next(iter(mgr.google.pending))
        self.assertTrue(mgr.google.pending[state]["mobile"])
        self.assertEqual(mgr.google.pending[state]["redirect"], "https://example.test/auth/google-app/cb")

    def test_pending_is_exposed_for_the_callbacks(self):
        # /oauth/callback and /auth/google-app/cb both read eng.plugins.pending
        mgr, _ = fake_mgr(google_defs(), {
            "auth": {"google_client_id": SERVER_CID, "google_client_secret": SERVER_CSEC},
        })
        self.assertIs(mgr.google.pending, PluginManager.pending.fget(mgr))


if __name__ == "__main__":
    unittest.main()
