"""Unit tests for core/auth.py (email/password + Google account sign-in)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import auth


class AccountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch("core.paths.data_dir", return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_account_initially(self):
        self.assertFalse(auth.has_account())
        self.assertEqual(auth.email(), "")

    def test_create_and_verify(self):
        auth.create_account("Owner@Example.com", "correct horse 123")
        self.assertTrue(auth.has_account())
        self.assertEqual(auth.email(), "owner@example.com")
        self.assertTrue(auth.verify_password("owner@example.com", "correct horse 123"))
        self.assertTrue(auth.verify_password("OWNER@example.com", "correct horse 123"))  # case-insensitive
        self.assertFalse(auth.verify_password("owner@example.com", "wrong"))
        self.assertFalse(auth.verify_password("someone@example.com", "correct horse 123"))

    def test_password_never_plaintext(self):
        auth.create_account("a@example.com", "super secret pw")
        raw = (Path(self.tmp.name) / "account.json").read_text()
        self.assertNotIn("super secret pw", raw)

    def test_invalid_email(self):
        with self.assertRaises(ValueError):
            auth.create_account("not-an-email", "longenough1")

    def test_too_short_password(self):
        with self.assertRaises(ValueError):
            auth.create_account("a@example.com", "short")

    def test_change_password(self):
        auth.create_account("a@example.com", "first password 1")
        auth.set_password("second password 2")
        self.assertFalse(auth.verify_password("a@example.com", "first password 1"))
        self.assertTrue(auth.verify_password("a@example.com", "second password 2"))

    def test_google_allow_list(self):
        auth.create_account("a@example.com", "password 123")
        self.assertEqual(auth.google_email(), "")
        acc = auth._read_account()
        acc["google_email"] = "owner@gmail.com"
        auth._write_account(acc)
        self.assertEqual(auth.google_email(), "owner@gmail.com")


if __name__ == "__main__":
    unittest.main()
