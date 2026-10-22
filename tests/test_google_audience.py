"""verify_google must reject ID tokens minted for a different OAuth client."""
import base64
import json
import time
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from core import auth as auth_mod


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class AudienceTests(unittest.TestCase):
    def setUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = self.key.public_key().public_numbers()
        self._orig = auth_mod._google_keys
        auth_mod._google_keys = lambda: [{
            "kid": "testkid",
            "n": _b64(pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")),
            "e": _b64(pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big")),
        }]

    def tearDown(self):
        auth_mod._google_keys = self._orig

    def _token(self, aud):
        header = _b64(json.dumps({"alg": "RS256", "kid": "testkid"}).encode())
        payload = _b64(json.dumps({
            "iss": "https://accounts.google.com", "aud": aud,
            "email": "owner@test.dev", "email_verified": True,
            "exp": time.time() + 600,
        }).encode())
        sig = self.key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{_b64(sig)}"

    def test_matching_audience_is_accepted(self):
        self.assertEqual(auth_mod.verify_google(self._token("my-client"), "my-client"), "owner@test.dev")

    def test_other_apps_token_is_rejected(self):
        with self.assertRaises(ValueError):
            auth_mod.verify_google(self._token("someone-elses-client"), "my-client")

    def test_audience_list_is_accepted(self):
        self.assertEqual(auth_mod.verify_google(self._token(["other", "my-client"]), "my-client"), "owner@test.dev")


if __name__ == "__main__":
    unittest.main()
