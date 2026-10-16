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
_FALLBACK_FILE = "auth_password.txt"


def _fallback_path():  # lazy import: core.paths is heavyweight for unit tests
    from core import paths
    return paths.data_dir() / _FALLBACK_FILE


def _read_fallback() -> str:
    try:
        return _fallback_path().read_text().strip()
    except OSError:
        return ""


def _write_fallback(stored: str) -> None:
    p = _fallback_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(stored)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS).hex()


def has_password() -> bool:
    return bool(secrets_mod.get_secret(SECRET_NAME)) or bool(_read_fallback())


def set_password(password: str) -> None:
    """Store a new password (overwrites any existing one).

    Prefers the OS credential store; on headless machines with no store
    (cloud VMs, Docker) falls back to a 0600 file holding only the salted
    hash — never the password itself.
    """
    pw = (password or "").strip()
    if len(pw) < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
    if len(pw) > 256:
        raise ValueError("Password is too long.")
    salt = os.urandom(16)
    stored = f"pbkdf2${_ITERATIONS}${salt.hex()}${_hash(pw, salt)}"
    try:
        secrets_mod.set_secret(SECRET_NAME, stored)
    except secrets_mod.SecretStoreError:
        _write_fallback(stored)


def _check(stored: str, password: str) -> bool:
    try:
        algo, iters, salt_hex, hexdigest = stored.split("$")
        if algo != "pbkdf2":
            return False
    except ValueError:
        return False
    cand = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt_hex), int(iters)).hex()
    return bool(stored) and hmac.compare_digest(cand, hexdigest)


def verify(password: str) -> bool:
    """Constant-time check of a password against the stored hash."""
    stored = secrets_mod.get_secret(SECRET_NAME) or _read_fallback()
    return _check(stored, password)


def credential_ok(presented: str, service_token: str) -> bool:
    """True if the presented credential is the service token or the password."""
    if presented and service_token and hmac.compare_digest(presented.encode(), service_token.encode()):
        return True
    return bool(presented) and verify(presented)
