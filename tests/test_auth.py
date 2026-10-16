"""Unit tests for core/auth.py (password login). Secrets backend is faked in-memory."""
import unittest
from unittest import mock

from core import auth


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.p = mock.patch.multiple("core.secrets", get_secret=self.store.get, set_secret=lambda n, v: self.store.__setitem__(n, v))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_no_password_initially(self):
        self.assertFalse(auth.has_password())
        self.assertFalse(auth.verify("anything"))

    def test_roundtrip(self):
        auth.set_password("correct horse 123")
        self.assertTrue(auth.has_password())
        self.assertTrue(auth.verify("correct horse 123"))
        self.assertFalse(auth.verify("wrong"))
        self.assertFalse(auth.verify(""))

    def test_too_short(self):
        with self.assertRaises(ValueError):
            auth.set_password("short")

    def test_hash_not_plaintext(self):
        auth.set_password("some secret pw")
        self.assertNotIn("some secret pw", self.store.get(auth.SECRET_NAME, ""))

    def test_credential_ok(self):
        auth.set_password("phone login 1")
        self.assertTrue(auth.credential_ok("TOK", "TOK"))       # service token still works
        self.assertTrue(auth.credential_ok("phone login 1", "TOK"))  # password works too
        self.assertFalse(auth.credential_ok("nope", "TOK"))
        self.assertFalse(auth.credential_ok("", "TOK"))

    def test_headless_fallback_file(self):
        """No credential store (cloud VM): the salted hash goes to a 0600 file, never plaintext."""
        import tempfile
        from pathlib import Path
        from core import secrets as secrets_mod
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(secrets_mod, "set_secret",
                                   side_effect=secrets_mod.SecretStoreError("no store")), \
                 mock.patch.object(secrets_mod, "get_secret", return_value=None), \
                 mock.patch("core.paths.data_dir", return_value=Path(td)):
                auth.set_password("cloud password 9")
                f = Path(td) / auth._FALLBACK_FILE
                self.assertTrue(f.is_file())
                self.assertNotIn("cloud password 9", f.read_text())
                self.assertTrue(auth.has_password())
                self.assertTrue(auth.verify("cloud password 9"))
                self.assertFalse(auth.verify("wrong"))


if __name__ == "__main__":
    unittest.main()
