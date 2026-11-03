"""Multi-account sign-in for the ZerotheBot app.

Two ways in:
  - Email + password — the account is created on signup; only a salted PBKDF2
    hash is stored, never the password itself.
  - Google — the Android app obtains an ID token from Google Play services and
    the backend verifies it against Google's public keys (RS256).

Each account lives in its own 0600 JSON file under ``data_dir/accounts/`` so
many users can sign up on the same server instance. The first account created
is treated as the admin/owner for backward compatibility (desktop scripts,
service token, etc.) but any account can log in and use the app.
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
_ACCOUNTS_DIR = "accounts"
_LEGACY_ACCOUNT_FILE = "account.json"
_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_CERT_CACHE_SECONDS = 3600


def _accounts_dir():  # lazy import: core.paths is heavyweight for unit tests
    from core import paths
    d = paths.data_dir() / _ACCOUNTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _account_path(email: str):
    from core import paths
    safe = (email or "").strip().lower().replace("@", "_at_").replace(".", "_dot_")
    return paths.data_dir() / _ACCOUNTS_DIR / (safe + ".json")


def _legacy_account_path():
    from core import paths
    return paths.data_dir() / _LEGACY_ACCOUNT_FILE


def _migrate_legacy() -> None:
    """Move a legacy single account.json into the accounts/ directory."""
    legacy = _legacy_account_path()
    if legacy.exists() and not any(_accounts_dir().glob("*.json")):
        try:
            data = json.loads(legacy.read_text())
        except (OSError, ValueError):
            return
        if data.get("email"):
            data["is_admin"] = True   # legacy sole owner becomes admin
            _write_account(data["email"], data)
            try:
                legacy.unlink()
            except OSError:
                pass


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS).hex()


def _read_account(email: str) -> dict:
    try:
        return json.loads(_account_path(email).read_text())
    except (OSError, ValueError):
        return {}


def _write_account(email: str, data: dict) -> None:
    p = _account_path(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def has_account(email: str | None = None) -> bool:
    """True if any account exists, or if the given email has an account."""
    _migrate_legacy()
    if email:
        return bool(_read_account(_normalize(email)).get("email"))
    return any(f.suffix == ".json" for f in _accounts_dir().glob("*.json"))


def list_accounts() -> list[str]:
    """Return every registered email (sorted)."""
    _migrate_legacy()
    out = []
    for f in sorted(_accounts_dir().glob("*.json")):
        try:
            acc = json.loads(f.read_text())
            if acc.get("email"):
                out.append(acc["email"])
        except (OSError, ValueError):
            pass
    return out


def email() -> str:
    """Backward-compat: return the first registered email (legacy "owner")."""
    accts = list_accounts()
    return accts[0] if accts else ""


def delete_account(email: str) -> None:
    """Remove the account credentials for the given email."""
    try:
        _account_path(_normalize(email)).unlink()
    except OSError:
        pass


def google_email(email: str) -> str:
    """Optional per-account Google allow-list: empty means any verified Google email may sign in."""
    return _read_account(_normalize(email)).get("google_email", "")


def set_google_email(email: str, addr: str) -> None:
    """Bind a Google address to the given account."""
    email = _normalize(email)
    acc = _read_account(email)
    if not acc.get("email"):
        raise ValueError("Create an account first.")
    acc["google_email"] = (addr or "").strip().lower()
    _write_account(email, acc)


def create_account(addr: str, password: str | None = None, *, google_only: bool = False, is_admin: bool = False) -> None:
    """Create an account for ``addr``.

    ``password`` is required unless ``google_only`` is set (Google sign-up).
    """
    addr = _normalize(addr)
    if "@" not in addr or "." not in addr.split("@")[-1]:
        raise ValueError("That email address does not look right.")
    if has_account(addr):
        raise ValueError("An account with that email already exists.")
    if not google_only:
        pw = password or ""
        if len(pw) < MIN_LENGTH:
            raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
        salt = os.urandom(16)
        acc = {
            "email": addr,
            "hash": f"pbkdf2${_ITERATIONS}${salt.hex()}${_hash_password(pw, salt)}",
            "google_only": False,
            "is_admin": bool(is_admin),
            "created_at": time.time(),
        }
    else:
        acc = {
            "email": addr,
            "hash": None,
            "google_only": True,
            "is_admin": bool(is_admin),
            "created_at": time.time(),
        }
    _write_account(addr, acc)


def set_admin(email: str, is_admin: bool = True) -> None:
    """Grant or revoke admin rights for an account."""
    email = _normalize(email)
    acc = _read_account(email)
    if not acc.get("email"):
        raise ValueError("No such account.")
    acc["is_admin"] = bool(is_admin)
    _write_account(email, acc)


def is_admin(email: str | None = None) -> bool:
    """True if the given email (or the first/legacy account) is an admin."""
    if email is None:
        accts = list_accounts()
        email = accts[0] if accts else ""
    if not email:
        return False
    return bool(_read_account(_normalize(email)).get("is_admin"))


def verify_password(email: str, password: str) -> bool:
    email = _normalize(email)
    acc = _read_account(email)
    if not acc.get("email") or not acc.get("hash"):
        return False
    try:
        _algo, iters, salt_hex, hexdigest = acc["hash"].split("$")
    except ValueError:
        return False
    cand = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt_hex), int(iters)).hex()
    return hmac.compare_digest(cand, hexdigest)


def set_password(email: str, password: str) -> None:
    """Change the password of the given account."""
    email = _normalize(email)
    acc = _read_account(email)
    if not acc.get("email"):
        raise ValueError("Create an account first.")
    if len(password or "") < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
    salt = os.urandom(16)
    acc["hash"] = f"pbkdf2${_ITERATIONS}${salt.hex()}${_hash_password(password, salt)}"
    acc["google_only"] = False
    _write_account(email, acc)


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


def verify_google(id_token: str, audience: str = "") -> str:
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
    if audience:
        aud = payload.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if audience not in [str(a) for a in auds]:
            raise ValueError("That Google sign-in was issued for a different app.")
    if payload.get("exp", 0) < time.time():
        raise ValueError("That Google sign-in expired; try again.")
    addr = payload.get("email")
    if not addr or not payload.get("email_verified"):
        raise ValueError("Google did not verify an email address for that account.")
    addr = addr.lower()
    # Per-account allow-list: if this account has google_email set, only that
    # address may sign in with Google. If not set, any verified Google email works.
    acc = _read_account(addr)
    allow = acc.get("google_email") if acc.get("email") else None
    if allow and addr != allow.lower():
        raise ValueError("That Google account is not linked to this app account.")
    return addr
