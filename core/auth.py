"""Password login for standalone (phone/cloud) use.

Single-user instance, Grok-style: the owner sets one password on first run
(from the phone itself), then signs in with it. No access tokens to copy,
no desktop app involved.

The service token still works everywhere it always has (desktop app, API);
the password is simply accepted anywhere a token is. After login the server
sets the usual session cookie, so nothing downstream changes.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from core import secrets as secrets_mod

SECRET_NAME = "auth:password"
MIN_LENGTH = 8
_ITERATIONS = 210_000


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS).hex()


def has_password() -> bool:
    return bool(secrets_mod.get_secret(SECRET_NAME))


def set_password(password: str) -> None:
    """Store a new password (overwrites any existing one)."""
    pw = (password or "").strip()
    if len(pw) < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
    if len(pw) > 256:
        raise ValueError("Password is too long.")
    salt = os.urandom(16)
    secrets_mod.set_secret(SECRET_NAME, f"pbkdf2${_ITERATIONS}${salt.hex()}${_hash(pw, salt)}")


def verify(password: str) -> bool:
    """Constant-time check of a password against the stored hash."""
    stored = secrets_mod.get_secret(SECRET_NAME) or ""
    try:
        algo, iters, salt_hex, hexdigest = stored.split("$")
        if algo != "pbkdf2":
            return False
    except ValueError:
        return False
    cand = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt_hex), int(iters)).hex()
    return bool(stored) and hmac.compare_digest(cand, hexdigest)


def credential_ok(presented: str, service_token: str) -> bool:
    """True if the presented credential is the service token or the password."""
    if presented and service_token and hmac.compare_digest(presented.encode(), service_token.encode()):
        return True
    return bool(presented) and verify(presented)
