"""Push notifications to the phone via Firebase Cloud Messaging (HTTP v1).

Activation needs two things the user supplies once:
  1. A Firebase service-account JSON, stored as the secret ``push:service_account``
     (Settings → Admin, or the secrets store directly).
  2. The app built with google-services.json so it can obtain an FCM token and
     register it here (POST /api/push/register).

Until either is missing everything here is a polite no-op: nothing crashes,
nothing is sent, and the in-app Inbox still shows every notification.
"""
from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING

from . import secrets
from .db import now

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_tok_cache: tuple[str, float] | None = None
_lock = threading.Lock()


class PushUnavailable(RuntimeError):
    pass


def _service_account_json() -> str:
    try:
        return str(secrets.get_secret("push:service_account") or "")
    except Exception:  # noqa: BLE001
        return ""


def _access_token(sa_json: str) -> str:
    """OAuth2 access token for the FCM v1 API, from the service account JWT."""
    global _tok_cache
    with _lock:
        if _tok_cache and _tok_cache[1] > time.time():
            return _tok_cache[0]
    import httpx
    sa = json.loads(sa_json)
    iat = int(time.time())
    assertion = None
    try:
        import jwt  # PyJWT
        assertion = jwt.encode({"iss": sa["client_email"], "scope": _SCOPE, "aud": sa["token_uri"],
                                "iat": iat, "exp": iat + 3600}, sa["private_key"], algorithm="RS256")
    except Exception:  # noqa: BLE001
        try:
            from google.oauth2 import service_account as gsa
            import google.auth.transport.requests as gtr
            creds = gsa.Credentials.from_service_account_info(sa, scopes=[_SCOPE])
            creds.refresh(gtr.Request())
            with _lock:
                _tok_cache = (creds.token, time.time() + 3000)
            return creds.token
        except Exception as e:  # noqa: BLE001
            raise PushUnavailable(f"FCM push needs PyJWT or google-auth installed ({e}).") from e
    r = httpx.post(sa["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                          "assertion": assertion}, timeout=30)
    if r.status_code >= 400:
        raise PushUnavailable(f"Google token exchange failed (HTTP {r.status_code}).")
    tok = r.json().get("access_token", "")
    if not tok:
        raise PushUnavailable("Google token exchange returned no access token.")
    with _lock:
        _tok_cache = (tok, time.time() + 3000)
    return tok


# -- token registry -----------------------------------------------------------
def register(db, owner: str, token: str, platform: str = "android") -> dict:
    token = (token or "").strip()
    if not token:
        raise ValueError("No push token given.")
    db.execute("INSERT INTO push_tokens(token, owner, platform, created_at, updated_at) VALUES(?,?,?,?,?) "
               "ON CONFLICT(token) DO UPDATE SET owner=excluded.owner, platform=excluded.platform, updated_at=excluded.updated_at",
               (token[:4096], owner or "", platform[:20], now(), now()))
    return {"ok": True, "registered": len(token)}


def unregister(db, owner: str, token: str) -> dict:
    db.execute("DELETE FROM push_tokens WHERE token=? AND (owner=? OR owner='' OR ?='')",
               ((token or "").strip()[:4096], owner or "", owner or ""))
    return {"ok": True}


def tokens_for(db, owner: str) -> list[str]:
    if owner:
        rows = db.query("SELECT token FROM push_tokens WHERE owner=? OR owner=''", (owner,))
    else:
        rows = db.query("SELECT token FROM push_tokens")
    return [r["token"] for r in rows]


# -- sending -------------------------------------------------------------------
def send(eng: "Engine", owner: str, title: str, body: str, thread_id: str = "", kind: str = "") -> int:
    """Send one notification to the owner's phones. Returns the number delivered."""
    sa = _service_account_json()
    if not sa:
        return 0
    try:
        toks = tokens_for(eng.db, owner)
    except Exception:  # noqa: BLE001
        return 0
    if not toks:
        return 0
    try:
        import httpx
        access = _access_token(sa)
        proj = json.loads(sa).get("project_id", "")
        sent = 0
        for t in toks:
            payload = {"message": {"token": t,
                                   "notification": {"title": title[:120], "body": body[:240]},
                                   "data": {"thread_id": thread_id or "", "kind": kind or ""}}}
            try:
                r = httpx.post(f"https://fcm.googleapis.com/v1/{proj}/messages:send",
                               headers={"Authorization": "Bearer " + access}, json=payload, timeout=15)
                if r.status_code in (404, 410):
                    eng.db.execute("DELETE FROM push_tokens WHERE token=?", (t,))   # stale uninstall
                elif r.status_code < 300:
                    sent += 1
            except Exception:  # noqa: BLE001
                continue
        return sent
    except PushUnavailable as e:
        eng.log.info("push skipped: %s", e)
        return 0
    except Exception as e:  # noqa: BLE001
        eng.log.info("push failed: %s", e)
        return 0
