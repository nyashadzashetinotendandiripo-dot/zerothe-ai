"""Account sign-in for the standalone phone/cloud app.

Two ways in:
  - Email + password — the account is created on first run; only a salted
    PBKDF2 hash is stored, never the password itself.
  - Google — the Android app obtains an ID token from Google Play services and
    the backend verifies it against Google's public keys (RS256), trusting the
    email it carries.

The backend is still single-owner: whoever signs in first owns the instance.
A Google sign-in is accepted for any email (the owner may restrict it to their
own address with ``google_email``).

On headless machines with no OS credential store (cloud VMs, Docker) the
account lives in a 0600 JSON file holding only the hash.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from core import secrets as secrets_mod

MIN_LENGTH = 8
_ITERATIONS = 210_000
_ACCOUNT_FILE = "account.json"
_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_CERT_CACHE_SECONDS = 3600


def _account_path():  # lazy import: core.paths is heavyweight for unit tests
    from core import paths
    return paths.data_dir() / _ACCOUNT_FILE


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS).hex()


def _read_account() -> dict:
    try:
        return json.loads(_account_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_account(data: dict) -> None:
    p = _account_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def has_account() -> bool:
    return bool(_read_account().get("email"))


def email() -> str:
    return _read_account().get("email", "")


def google_email() -> str:
    """Optional allow-list: empty means any verified Google email may sign in."""
    return _read_account().get("google_email", "")


def create_account(addr: str, password: str) -> None:
    addr = (addr or "").strip().lower()
    if "@" not in addr or "." not in addr.split("@")[-1]:
        raise ValueError("That email address does not look right.")
    pw = password or ""
    if len(pw) < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
    salt = os.urandom(16)
    acc = _read_account()
    acc["email"] = addr
    acc["hash"] = f"pbkdf2${_ITERATIONS}${salt.hex()}${_hash_password(pw, salt)}"
    _write_account(acc)


def verify_password(addr: str, password: str) -> bool:
    acc = _read_account()
    if not acc.get("email") or not acc.get("hash"):
        return False
    if (addr or "").strip().lower() != acc["email"]:
        return False
    try:
        _algo, iters, salt_hex, hexdigest = acc["hash"].split("$")
    except ValueError:
        return False
    cand = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt_hex), int(iters)).hex()
    return hmac.compare_digest(cand, hexdigest)


def set_password(password: str) -> None:
    """Change the password of the existing account."""
    acc = _read_account()
    if not acc.get("email"):
        raise ValueError("Create an account first.")
    if len(password or "") < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
    salt = os.urandom(16)
    acc["hash"] = f"pbkdf2${_ITERATIONS}${salt.hex()}${_hash_password(password, salt)}"
    _write_account(acc)


# --------------------------------------------------------------------- Google
_certs_cache: dict = {"at": 0.0, "keys": []}


def _google_keys() -> list[dict]:
    if time.time() - _certs_cache["at"] < _CERT_CACHE_SECONDS and _certs_cache["keys"]:
        return _certs_cache["keys"]
    import httpx

    r = httpx.get(_GOOGLE_CERTS_URL, timeout=15.0)
    r.raise_for_status()
    keys = list(r.json().get("keys", []))
    _certs_cache["at"] = time.time()
    _certs_cache["keys"] = keys
    return keys


def verify_google(id_token: str) -> str:
    """Verify a Google ID token; return the verified email. Raises ValueError."""
    if not id_token or id_token.count(".") != 2:
        raise ValueError("That Google sign-in did not come through.")
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as e:  # pragma: no cover
        raise ValueError("Google sign-in needs the 'cryptography' package on the server.") from e

    h_b64, p_b64, s_b64 = id_token.split(".")
    try:
        header = json.loads(_b64url_decode(h_b64))
        payload = json.loads(_b64url_decode(p_b64))
    except ValueError as e:
        raise ValueError("That Google sign-in could not be read.") from e

    kid = header.get("kid")
    key = next((k for k in _google_keys() if k.get("kid") == kid), None)
    if not key:
        raise ValueError("Google's signing key changed; try again.")
    try:
        n = int.from_bytes(_b64url_decode(key["n"]), "big")
        e = int.from_bytes(_b64url_decode(key["e"]), "big")
        pub = rsa.RSAPublicNumbers(e, n).public_key()
        pub.verify(_b64url_decode(s_b64), f"{h_b64}.{p_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:  # noqa: BLE001
        raise ValueError("That Google sign-in could not be verified.") from e

    if payload.get("iss") not in _GOOGLE_ISSUERS:
        raise ValueError("That Google sign-in is from the wrong issuer.")
    if payload.get("exp", 0) < time.time():
        raise ValueError("That Google sign-in expired; try again.")
    addr = payload.get("email")
    if not addr or not payload.get("email_verified"):
        raise ValueError("Google did not verify an email address for that account.")
    allow = google_email()
    if allow and addr.lower() != allow.lower():
        raise ValueError("That Google account is not the owner of this app.")
    return addr.lower()
