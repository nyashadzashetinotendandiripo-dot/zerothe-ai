"""Plugins and connectors.

Built-in connectors: Gmail, Google Calendar, Slack, Notion, Linear, Jira, GitHub, and a generic REST connector.
Third-party plugins live in folders with a plugin.json manifest and can be installed from a local folder or git URL:

  * declarative  - tools described as HTTP requests in plugin.json (no code)
  * python       - plugin.json has "entry": "plugin.py" exposing register(api) (runs in-process: only install code you trust)

Bots only see a plugin's tools after the user grants the plugin to that Bot.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import inspect
import io
import json
import mimetypes
import os
import re
import secrets as pysecrets
import shlex
import shutil
import subprocess
import threading
import time
import textwrap
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote, urlencode

from . import paths, sandbox, secrets
from .settings import plugin_allowed
from .tooling import Risk, ToolContext, ToolResult, ToolSpec, b, i, obj, s, safe_name, short

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine


class ConnectorError(RuntimeError):
    pass


@dataclass
class PluginDef:
    id: str
    name: str
    description: str
    kind: str = "builtin"          # builtin | declarative | python | rest
    version: str = "1.0.0"
    fields: list[dict] = field(default_factory=list)   # {key,label,secret,required,placeholder,default}
    oauth: dict | None = None      # {"provider":"google","scopes":[...]}
    setup_url: str = ""            # browser link that starts the vendor-side setup (Connect or token page)
    setup_label: str = ""          # short label for the setup link button
    build: Callable[["Conn"], list[ToolSpec]] | None = None
    check: Callable[["Conn"], str] | None = None
    path: Path | None = None
    source: str = "builtin"
    skills: list[str] = field(default_factory=list)
    help: str = ""


def _res(data: Any, source: str, limit: int = 20000) -> ToolResult:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
    if len(text) > limit:
        text = text[:limit] + f"\n[... truncated {len(text) - limit} chars ...]"
    return ToolResult(text, untrusted=source)


def T(name: str, desc: str, props: dict, required: list[str], fn: Callable[[ToolContext, dict], Any], *, risk=None,
      label: Callable[[dict], str] | None = None, read: bool = False, untrusted: str = "") -> ToolSpec:
    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        out = fn(ctx, args)
        if isinstance(out, ToolResult):
            return out
        return _res(out, untrusted) if untrusted else ToolResult(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)[:8000])
    return ToolSpec(name, desc, obj(props, required), handler, risk=risk, label=label or (lambda a, n=name: n), read_only=read)


def R(category: str, summary: Callable[[dict], str], pattern: Callable[[dict], str] | None = None, never_auto: bool = False,
      detail: Callable[[dict], dict] | None = None):
    def fn(ctx: ToolContext, args: dict) -> Risk:
        d = dict(detail(args)) if detail else {}
        d["pattern"] = pattern(args) if pattern else "*"
        return Risk(category, summary(args), d, never_auto=never_auto)
    return fn


# ----------------------------------------------------------------------------- Google OAuth
# --------------------------------------------------------------------------- credential keys
def _ckey(pid: str, owner: str, key: str) -> str:
    """Credential-store key for a plugin secret.

    Owner-scoped (``plugin:{pid}:{owner}:{key}``) when the acting Bot's owner is
    known, so two users connecting the same plugin never share one OAuth identity.
    The legacy global form (``plugin:{pid}:{key}``) remains the store for
    server-wide pasted credentials and for everything connected before
    owner-scoping existed.
    """
    if owner:
        return f"plugin:{pid}:{owner}:{key}"
    return f"plugin:{pid}:{key}"


def _cget(pid: str, owner: str, key: str) -> str:
    """Read a credential: the owner's own first, then the legacy global one."""
    if owner:
        v = secrets.get_secret(_ckey(pid, owner, key))
        if v:
            return v
    return secrets.get_secret(f"plugin:{pid}:{key}") or ""


def _cset(pid: str, owner: str, key: str, val: str) -> None:
    secrets.set_secret(_ckey(pid, owner, key), val)


class GoogleOAuth:
    AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"

    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def auth_url(self, plugin_id: str, client_id: str, scopes: list[str], redirect_uri: str, *, auth_url: str = AUTH,
                 token_url: str = TOKEN, secret_style: str = "body", extra: dict | None = None,
                 mobile: bool = False, owner: str = "") -> str:
        state = pysecrets.token_urlsafe(24)
        verifier = pysecrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.pending[state] = {"plugin": plugin_id, "verifier": verifier, "redirect": redirect_uri, "ts": time.time(),
                               "token_url": token_url, "secret_style": secret_style, "mobile": bool(mobile),
                               "owner": owner or ""}
        q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
             "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        q.update(extra or {})
        return f"{auth_url}?{urlencode(q)}"

    def exchange(self, state: str, code: str, client_id: str, client_secret: str, *, token_url: str | None = None,
                 secret_style: str = "body", owner: str = "") -> str:
        import httpx
        p = self.pending.pop(state, None)
        if not p or time.time() - p["ts"] > 900:
            raise ConnectorError("This sign-in link expired or was not started here. Start again from Plugins.")
        payload = {"code": code, "redirect_uri": p["redirect"], "grant_type": "authorization_code", "code_verifier": p["verifier"]}
        headers = None
        if secret_style == "basic" and client_secret:
            headers = {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()}
        else:   # public clients (PKCE, no secret) authenticate with the client id in the body
            payload["client_id"] = client_id
            if client_secret:
                payload["client_secret"] = client_secret
        r = httpx.post(token_url or self.TOKEN, data=payload, headers=headers, timeout=30)
        if r.status_code >= 400:
            raise ConnectorError(f"Google rejected the sign-in: {short(r.text, 200)}")
        data = r.json()
        if not data.get("access_token"):
            raise ConnectorError(f"Sign-in failed: {short(json.dumps(data, default=str)[:200], 200)}")
        if data.get("refresh_token"):
            _cset(p["plugin"], owner, "refresh_token", data["refresh_token"])
        else:
            # Providers with long-lived access tokens (e.g. Asana) may not rotate:
            # keep the access token as the stored credential so Connect still counts.
            _cset(p["plugin"], owner, "refresh_token", data["access_token"])
            _cset(p["plugin"], owner, "token", data["access_token"])
        # Some vendors (Salesforce) require the original redirect_uri
        # on later refresh calls, so remember which one this sign-in used.
        try:
            secrets.set_secret(f"plugin:{p['plugin']}:redirect_uri", p["redirect"])
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._cache[(p["plugin"], owner)] = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 60)
        return p["plugin"]

    def access_token(self, plugin_id: str, client_id: str, client_secret: str, owner: str = "") -> str:
        import httpx
        with self._lock:
            tok = self._cache.get((plugin_id, owner))
            if tok and tok[1] > time.time():
                return tok[0]
        refresh = _cget(plugin_id, owner, "refresh_token")
        if not refresh:
            raise ConnectorError("Not signed in to Google. Open Plugins and press Connect.")
        r = httpx.post(self.TOKEN, data={"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh,
                                         "grant_type": "refresh_token"}, timeout=30)
        if r.status_code >= 400:
            raise ConnectorError(f"Google sign-in expired ({short(r.text, 120)}). Open Plugins and press Connect again.")
        data = r.json()
        with self._lock:
            self._cache[(plugin_id, owner)] = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 60)
        return data["access_token"]


_OAUTH2_CACHE: dict[str, tuple[dict, float]] = {}


def google_client(mgr: "PluginManager", pid: str) -> tuple[str, str]:
    """The Google OAuth client to use for a plugin's Connect sign-in.

    Order: the server-wide client (the one used for "Continue with Google",
    so Gmail/Calendar/Docs need no per-plugin setup at all), then the plugin's
    own pasted client, then Gmail's (the historical fallback). Returning the
    server client first means one redirect URI, one consent screen, zero
    console work on the phone.
    """
    d = mgr.defs.get(pid)
    if not d or (d.oauth or {}).get("provider") != "google":
        return "", ""
    auth = mgr.settings.get("auth") or {}
    if isinstance(auth, dict) and auth.get("google_client_id") and auth.get("google_client_secret"):
        return str(auth["google_client_id"]), str(auth["google_client_secret"])
    cid = mgr.config_value(pid, "client_id")
    csec = mgr.config_value(pid, "client_secret")
    if pid != "gmail":
        cid = cid or mgr.config_value("gmail", "client_id")
        csec = csec or mgr.config_value("gmail", "client_secret")
    return cid or "", csec or ""



def stored_redirect_uri(pid: str) -> str:
    """The redirect URI a plugin's Connect sign-in used (remembered at exchange).

    Some providers (Salesforce) require it back on refresh_token grants.
    """
    try:
        return secrets.get_secret(f"plugin:{pid}:redirect_uri") or ""
    except Exception:  # noqa: BLE001
        return ""


def oauth2_token(c: Conn, pid: str, token_url: str, payload: dict, headers: dict | None = None,
                 rotate_field: str | None = "refresh_token") -> dict:
    """Generic OAuth2 token grant for plugins (refresh_token, password, account_credentials...).

    Caches the token response per plugin id. When the provider rotates the credential named by
    rotate_field (Xero, QuickBooks and Sage hand back a new refresh token on every refresh),
    the new value is written back to the credential store. Raises ConnectorError on failure.
    """
    now = time.time()
    owner = getattr(c, "owner", "") or ""
    hit = _OAUTH2_CACHE.get((pid, owner))
    if hit and hit[1] > now:
        return hit[0]
    import httpx
    try:
        r = httpx.post(token_url, data=payload, headers=headers, timeout=30)
    except Exception as e:  # noqa: BLE001
        raise ConnectorError(f"Could not reach the sign-in server: {e}")
    if r.status_code >= 400:
        raise ConnectorError(f"Sign-in failed (HTTP {r.status_code}). Check the plugin credentials in Plugins. {short(r.text, 200)}")
    try:
        data = r.json()
    except ValueError:
        raise ConnectorError("Sign-in returned an unexpected answer.")
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ConnectorError(f"Sign-in failed: {short(json.dumps(data, default=str)[:200], 200)}")
    _OAUTH2_CACHE[(pid, owner)] = (data, now + int(data.get("expires_in", 3600) or 3600) - 60)
    if rotate_field and data.get(rotate_field) and data[rotate_field] != payload.get(rotate_field):
        try:
            _cset(pid, owner, rotate_field, data[rotate_field])
        except Exception:  # noqa: BLE001
            pass
    return data


# ----------------------------------------------------------------------------- per-plugin runtime
class Conn:
    """What a plugin's tool builder uses: config, secrets, and a policy-checked HTTP client."""

    def __init__(self, mgr: "PluginManager", pdef: PluginDef, owner: str = ""):
        self.mgr, self.pdef = mgr, pdef
        # The Bot owner whose credentials this connection uses. Empty means the
        # legacy server-wide store (pasted credentials, pre-scoping connects).
        self.owner = owner or ""

    def cfg(self, key: str, default: str = "") -> str:
        # Declarative OAuth first: {{secret.token}} resolves to a fresh access
        # token when the user signed in via Connect (refresh_token stored),
        # so Connect wins over an older pasted token. Falls back to the stored
        # token when the refresh is unavailable or fails.
        if key == "token":
            o = self.pdef.oauth or {}
            rt = self.mgr.config_value(self.pdef.id, "refresh_token", owner=self.owner)
            if o and rt:
                try:
                    if (o.get("provider") or "") == "google":
                        cid, csec = google_client(self.mgr, self.pdef.id)
                    else:
                        cid = self.mgr.config_value(self.pdef.id, "client_id")
                        csec = self.mgr.config_value(self.pdef.id, "client_secret")
                    payload: dict[str, str] = {"grant_type": "refresh_token", "refresh_token": rt}
                    headers: dict[str, str] | None = None
                    if (o.get("secret_style") or "body") == "basic" and csec:
                        headers = {"Authorization": "Basic " + base64.b64encode(f"{cid}:{csec}".encode()).decode()}
                    else:
                        if cid:
                            payload["client_id"] = cid
                        if csec:
                            payload["client_secret"] = csec
                    data = oauth2_token(self, self.pdef.id, o.get("token_url") or GoogleOAuth.TOKEN, payload, headers=headers)
                    if data.get("access_token"):
                        return data["access_token"]
                except Exception:  # noqa: BLE001
                    pass
        return self.mgr.config_value(self.pdef.id, key) or default

    def http(self, ctx: ToolContext, method: str, url: str, *, headers: dict | None = None, params: dict | None = None,
             json_body: Any = None, data: Any = None, timeout: float = 30) -> Any:
        import httpx
        ok, why = self.mgr.engine.net.check(ctx.bot["id"], url)
        if not ok:
            raise ConnectorError(f"Blocked by network policy: {why}")
        try:
            r = httpx.request(method, url, headers=headers, params=params, json=json_body, data=data, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as e:
            raise ConnectorError(f"Could not reach {self.pdef.name}: {e}") from e
        if r.status_code in (401, 403):
            raise ConnectorError(f"{self.pdef.name} refused the request (HTTP {r.status_code}). Check the credentials/permissions in Plugins. {short(r.text, 200)}")
        if r.status_code == 429:
            raise ConnectorError(f"{self.pdef.name} rate-limited the request. Wait and retry.")
        if r.status_code >= 400:
            raise ConnectorError(f"{self.pdef.name} error {r.status_code}: {short(r.text, 400)}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return r.text

    def google_headers(self) -> dict:
        cid, csec = google_client(self.mgr, self.pdef.id)
        if not cid:
            cid, csec = self.cfg("client_id"), self.cfg("client_secret")
        return {"Authorization": "Bearer " + self.mgr.google.access_token(self.pdef.id, cid, csec, owner=self.owner)}


# ----------------------------------------------------------------------------- built-in connectors
def _b64(txt: str) -> str:
    return base64.urlsafe_b64encode(txt.encode("utf-8")).decode()


def _gmail_text(payload: dict) -> str:
    def walk(p: dict, want: str) -> str:
        if p.get("mimeType", "").startswith(want) and p.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(p["body"]["data"] + "===").decode("utf-8", "replace")
        for sub in p.get("parts", []) or []:
            t = walk(sub, want)
            if t:
                return t
        return ""
    text = walk(payload, "text/plain")
    if not text:
        h = walk(payload, "text/html")
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", h))
        text = re.sub(r"\s+", " ", text)
    return text


def _gmail_parts(payload: dict) -> list[dict]:
    """All MIME parts of a Gmail payload that carry a filename (i.e. attachments, inline or not)."""
    out: list[dict] = []
    for p in payload.get("parts", []) or []:
        if p.get("filename"):
            out.append(p)
        out.extend(_gmail_parts(p))
    if payload.get("filename") and payload not in out:
        out.append(payload)
    return out


def build_gmail(c: Conn) -> list[ToolSpec]:
    base = "https://gmail.googleapis.com/gmail/v1/users/me"

    def H() -> dict:
        return c.google_headers()

    def search(ctx, a):
        res = c.http(ctx, "GET", f"{base}/messages", headers=H(), params={"q": a.get("query", ""), "maxResults": min(int(a.get("max_results", 10)), 25)})
        out = []
        for m in res.get("messages", []):
            d = c.http(ctx, "GET", f"{base}/messages/{m['id']}", headers=H(),
                       params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]})
            hd = {x["name"]: x["value"] for x in d.get("payload", {}).get("headers", [])}
            out.append({"id": d["id"], "thread_id": d["threadId"], "from": hd.get("From"), "to": hd.get("To"), "subject": hd.get("Subject"),
                        "date": hd.get("Date"), "snippet": d.get("snippet"), "labels": d.get("labelIds")})
        return out or "No messages matched."

    def read(ctx, a):
        d = c.http(ctx, "GET", f"{base}/messages/{a['message_id']}", headers=H(), params={"format": "full"})
        hd = {x["name"]: x["value"] for x in d.get("payload", {}).get("headers", [])}
        parts = _gmail_parts(d.get("payload", {}))
        atts = [{"filename": p.get("filename"), "mime": p.get("mimeType"),
                 "size": (p.get("body") or {}).get("size"), "attachment_id": (p.get("body") or {}).get("attachmentId")}
                for p in parts if p.get("filename")]
        return {"id": d["id"], "thread_id": d["threadId"], "from": hd.get("From"), "to": hd.get("To"), "cc": hd.get("Cc"),
                "subject": hd.get("Subject"), "date": hd.get("Date"), "message_id_header": hd.get("Message-ID"),
                "attachments": atts, "body": _gmail_text(d.get("payload", {}))[:10000]}

    def get_attachment(ctx, a):
        d = c.http(ctx, "GET", f"{base}/messages/{a['message_id']}", headers=H(), params={"format": "full"})
        parts = [p for p in _gmail_parts(d.get("payload", {})) if (p.get("body") or {}).get("attachmentId")]
        want = (a.get("attachment_id") or "").strip()
        fname = (a.get("filename") or "").strip().lower()
        part = next((p for p in parts if str((p.get("body") or {}).get("attachmentId")) == want), None) \
            or next((p for p in parts if (p.get("filename") or "").lower() == fname), None)
        if not part:
            names = ", ".join(p.get("filename") or "?" for p in parts) or "none"
            return {"error": "No such attachment on that message.", "available": names}
        att = c.http(ctx, "GET", f"{base}/messages/{a['message_id']}/attachments/{part['body']['attachmentId']}", headers=H())
        raw = base64.urlsafe_b64decode((att.get("data") or "") + "===")
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", part.get("filename") or "attachment").strip()[:80] or "attachment.bin"
        folder = Path(ctx.engine.computer.workspace) / "shared" / "attachments" / re.sub(r"[^A-Za-z0-9_-]", "_", a["message_id"])
        folder.mkdir(parents=True, exist_ok=True)
        fp = folder / safe
        fp.write_bytes(raw)
        return f"Saved {len(raw)} bytes to {fp} ({part.get('mimeType') or 'file'}). Read it with fs_read if it is text, " \
               "or attach it to an email with gmail_create_draft / gmail_send attachments."

    def mime(ctx, a) -> str:
        m = EmailMessage()
        m["To"], m["Subject"] = a["to"], a.get("subject", "")
        if a.get("cc"):
            m["Cc"] = a["cc"]
        if a.get("in_reply_to"):
            m["In-Reply-To"] = m["References"] = a["in_reply_to"]
        m.set_content(a.get("body", ""))
        ws = Path(ctx.engine.computer.workspace)
        for rel in a.get("attachments") or []:
            fp = Path(str(rel))
            if not fp.is_absolute():
                fp = ws / fp
            if not fp.is_file():
                raise ConnectorError(f"Attachment not found in the workspace: {rel}")
            if fp.stat().st_size > 20_000_000:
                raise ConnectorError(f"Attachment is over Gmail's 20 MB limit: {rel}")
            mt = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
            maintype, _, subtype = mt.partition("/")
            m.add_attachment(fp.read_bytes(), maintype=maintype, subtype=subtype, filename=fp.name)
        return base64.urlsafe_b64encode(m.as_bytes()).decode()

    def draft(ctx, a):
        msg: dict[str, Any] = {"raw": mime(ctx, a)}
        if a.get("thread_id"):
            msg["threadId"] = a["thread_id"]
        d = c.http(ctx, "POST", f"{base}/drafts", headers=H(), json_body={"message": msg})
        return f"Draft created (id {d['id']}). It has NOT been sent."

    def send(ctx, a):
        msg: dict[str, Any] = {"raw": mime(ctx, a)}
        if a.get("thread_id"):
            msg["threadId"] = a["thread_id"]
        d = c.http(ctx, "POST", f"{base}/messages/send", headers=H(), json_body=msg)
        return f"Email sent (id {d.get('id')})."

    def send_draft(ctx, a):
        d = c.http(ctx, "POST", f"{base}/drafts/send", headers=H(), json_body={"id": a["draft_id"]})
        return f"Draft sent (message id {d.get('id')})."

    def label(ctx, a):
        c.http(ctx, "POST", f"{base}/messages/{a['message_id']}/modify", headers=H(),
               json_body={"addLabelIds": a.get("add", []), "removeLabelIds": a.get("remove", [])})
        return "Labels updated."

    def delete_email(ctx, a):
        mid = a["message_id"]
        try:
            c.http(ctx, "DELETE", f"{base}/messages/{mid}", headers=H())
            return "Email permanently deleted."
        except ConnectorError as e:
            text = str(e)
            if "403" not in text and "insufficientPermissions" not in text:
                raise
        # gmail.modify can trash but not permanently delete. Trash still removes
        # the mail from the inbox and is recoverable, so do that instead of failing.
        c.http(ctx, "POST", f"{base}/messages/{mid}/trash", headers=H())
        return ("Email moved to Trash (recoverable for 30 days). Permanent delete needs the full "
                "Gmail scope: Plugins Hub → Gmail → Disconnect, then Connect again to re-authorize.")

    mail_props = {"to": s("Recipient address(es), comma separated"), "subject": s("Subject"), "body": s("Plain-text body"),
                  "cc": s("Cc addresses"), "thread_id": s("Gmail thread id when replying"), "in_reply_to": s("Message-ID header being replied to"),
                  "attachments": {"type": "array", "items": {"type": "string"},
                                  "description": "Workspace file paths to attach (max 20 MB each), e.g. shared/jobhunt/cv.pdf"}}
    send_risk = R("send", lambda a: f"Send email to {a.get('to')}: \"{short(a.get('subject', ''), 80)}\"", lambda a: str(a.get("to", "*")).lower(),
                  detail=lambda a: {"to": a.get("to"), "cc": a.get("cc"), "subject": a.get("subject"), "body": short(a.get("body", ""), 1500)})
    return [
        T("gmail_search", "Search Gmail with Gmail query syntax (e.g. 'is:unread newer_than:2d'). Returns message summaries.",
          {"query": s("Gmail search query"), "max_results": i("Max results (<=25)")}, ["query"], search, read=True, untrusted="Gmail",
          label=lambda a: f"Search Gmail: {a.get('query', '')}"),
        T("gmail_read", "Read one email by id (lists any attachments; save one with gmail_get_attachment).", {"message_id": s("Message id")}, ["message_id"], read, read=True, untrusted="an email",
          label=lambda a: "Read an email"),
        T("gmail_get_attachment", "Download an attachment from an email into shared/attachments/ in the workspace.",
          {"message_id": s("Message id"), "filename": s("Attachment filename (or use attachment_id)"), "attachment_id": s("Attachment id from gmail_read")},
          ["message_id"], get_attachment,
          risk=R("download", lambda a: f"Save attachment {a.get('filename') or a.get('attachment_id') or ''} from an email",
                 lambda a: str(a.get("filename") or a.get("attachment_id") or "*")),
          label=lambda a: f"Save attachment {a.get('filename') or a.get('attachment_id') or ''}"),
        T("gmail_create_draft", "Create a draft email (not sent). Use this to propose replies.", mail_props, ["to", "subject", "body"], draft,
          label=lambda a: f"Draft email to {a.get('to')}"),
        T("gmail_send", "Send an email. Requires user approval.", mail_props, ["to", "subject", "body"], send, risk=send_risk,
          label=lambda a: f"Send email to {a.get('to')}"),
        T("gmail_send_draft", "Send an existing draft by id. Requires user approval.", {"draft_id": s("Draft id")}, ["draft_id"], send_draft,
          risk=R("send", lambda a: f"Send Gmail draft {a.get('draft_id')}", lambda a: "draft"), label=lambda a: "Send a draft"),
        T("gmail_label", "Add/remove labels on a message (e.g. archive = remove INBOX, mark read = remove UNREAD).",
          {"message_id": s("Message id"), "add": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "array", "items": {"type": "string"}}},
          ["message_id"], label, label=lambda a: "Update Gmail labels"),
        T("gmail_delete_email", "Permanently delete an email by id. Requires user approval.",
          {"message_id": s("Message id")}, ["message_id"], delete_email,
          risk=R("delete", lambda a: f"Delete Gmail email {a.get('message_id')}", lambda a: "email"),
          label=lambda a: "Delete an email"),
    ]


def build_gdocs(c: Conn) -> list[ToolSpec]:
    """Google Docs, Sheets and Slides over the Google REST APIs."""
    DOCS = "https://docs.googleapis.com/v1/documents"
    SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
    SLIDES = "https://slides.googleapis.com/v1/presentations"
    DRIVE = "https://www.googleapis.com/drive/v3/files"
    _KIND_MIME = {"doc": "application/vnd.google-apps.document",
                  "sheet": "application/vnd.google-apps.spreadsheet",
                  "slide": "application/vnd.google-apps.presentation"}

    def H() -> dict:
        return c.google_headers()

    def search(ctx, a):
        kind = a.get("kind") if a.get("kind") in _KIND_MIME else "doc"
        q = f"mimeType='{_KIND_MIME[kind]}' and trashed=false"
        if a.get("name"):
            q += " and name contains '" + str(a["name"]).replace("'", "\\'") + "'"
        res = c.http(ctx, "GET", DRIVE, headers=H(),
                     params={"q": q, "pageSize": 20, "orderBy": "modifiedTime desc",
                             "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"})
        out = [{"id": f.get("id"), "name": f.get("name"), "url": f.get("webViewLink"), "modified": f.get("modifiedTime")}
               for f in res.get("files", [])]
        return out or f"No Google {kind}s found."

    def doc_create(ctx, a):
        d = c.http(ctx, "POST", DOCS, headers=H(), json_body={"title": a["title"]})
        did = d["documentId"]
        if a.get("body"):
            c.http(ctx, "POST", f"{DOCS}/{did}:batchUpdate", headers=H(),
                   json_body={"requests": [{"insertText": {"endOfSegmentLocation": {"segmentId": ""}, "text": str(a["body"])}}]})
        return {"documentId": did, "title": a["title"], "url": f"https://docs.google.com/document/d/{did}/edit"}

    def doc_read(ctx, a):
        d = c.http(ctx, "GET", f"{DOCS}/{a['document_id']}", headers=H())
        chunks = []
        for el in d.get("body", {}).get("content", []):
            for pe in el.get("paragraph", {}).get("paragraphElements", []):
                tr = pe.get("textRun") or {}
                if tr.get("content"):
                    chunks.append(tr["content"])
        return {"title": d.get("title"), "text": "".join(chunks)[:20000] or "(empty document)"}

    def doc_append(ctx, a):
        text = str(a.get("text") or "")
        c.http(ctx, "POST", f"{DOCS}/{a['document_id']}:batchUpdate", headers=H(),
               json_body={"requests": [{"insertText": {"endOfSegmentLocation": {"segmentId": ""}, "text": text}}]})
        return f"Appended {len(text)} characters to the document."

    def sheet_read(ctx, a):
        rng = a.get("range") or "A1:Z100"
        r = c.http(ctx, "GET", f"{SHEETS}/{a['spreadsheet_id']}/values/{quote(rng)}", headers=H(),
                   params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"})
        vals = r.get("values") or []
        lines = [" | ".join(str(cell) for cell in row) for row in vals]
        return {"range": r.get("range"), "rows": len(vals), "text": "\n".join(lines)[:12000] or "Empty range."}

    def sheet_create(ctx, a):
        sh = c.http(ctx, "POST", SHEETS, headers=H(), json_body={"properties": {"title": a["title"]}})
        sid = sh["spreadsheetId"]
        rows = a.get("rows") or []
        if rows:
            c.http(ctx, "PUT", f"{SHEETS}/{sid}/values/Sheet1!A1", headers=H(),
                   params={"valueInputOption": "RAW"},
                   json_body={"range": "Sheet1!A1", "majorDimension": "ROWS", "values": rows})
        return {"spreadsheetId": sid, "title": a["title"], "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"}

    def sheet_append(ctx, a):
        cells = [str(x) for x in (a.get("cells") or [])]
        rng = a.get("range") or "Sheet1!A:Z"
        c.http(ctx, "POST", f"{SHEETS}/{a['spreadsheet_id']}/values/{quote(rng)}:append", headers=H(),
               params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
               json_body={"range": rng, "majorDimension": "ROWS", "values": [cells]})
        return f"Appended a row with {len(cells)} cells."

    def slide_create(ctx, a):
        p = c.http(ctx, "POST", SLIDES, headers=H(), json_body={"title": a["title"]})
        pid = p["presentationId"]
        reqs: list[dict] = []
        for n, sl in enumerate(a.get("slides") or [], start=1):
            sid = f"gb_s{n}_{pysecrets.token_hex(3)}"
            tb = f"gb_t{n}_{pysecrets.token_hex(3)}"
            reqs.append({"createSlide": {"objectId": sid, "insertionIndex": n,
                                         "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
            reqs.append({"createShape": {"objectId": tb, "shapeType": "TEXT_BOX",
                                         "elementProperties": {
                                             "size": {"width": {"magnitude": 11277600, "unit": "EMU"},
                                                      "height": {"magnitude": 6400800, "unit": "EMU"}},
                                             "transform": {"scaleX": 1, "scaleY": 1, "translateX": 457200,
                                                           "translateY": 457200, "unit": "EMU"}}}})
            lines = ([str(sl["title"])] if sl.get("title") else []) + [f"• {b}" for b in (sl.get("bullets") or [])]
            if lines:
                reqs.append({"insertText": {"objectId": tb, "text": "\n".join(lines), "insertionIndex": 0}})
        if reqs:
            c.http(ctx, "POST", f"{SLIDES}/{pid}:batchUpdate", headers=H(), json_body={"requests": reqs})
        return {"presentationId": pid, "url": f"https://docs.google.com/presentation/d/{pid}/edit",
                "slides_added": len(a.get("slides") or [])}

    write_risk = lambda cat, what: R(cat, what, lambda a: str(a.get("title") or a.get("document_id") or a.get("spreadsheet_id") or "*"))  # noqa: E731
    return [
        T("gdocs_search", "Find Google Docs/Sheets/Slides in My Drive (files made with this app).",
          {"kind": s("doc | sheet | slide"), "name": s("Only files whose name contains this")}, [], search, read=True,
          label=lambda a: f"Find Google {a.get('kind') or 'doc'}s: {a.get('name') or 'all'}"),
        T("gdocs_create", "Create a Google Doc, optionally with body text.", {"title": s("Document title"), "body": s("Initial text")},
          ["title"], doc_create, risk=write_risk("write", lambda a: f"Create Google Doc '{a.get('title')}'"),
          label=lambda a: f"Create Google Doc {a.get('title')}"),
        T("gdocs_read", "Read a Google Doc as plain text.", {"document_id": s("Document id")}, ["document_id"], doc_read, read=True,
          label=lambda a: "Read a Google Doc"),
        T("gdocs_append", "Append text to the end of a Google Doc.",
          {"document_id": s("Document id"), "text": s("Text to append")}, ["document_id", "text"], doc_append,
          risk=write_risk("write", lambda a: f"Append text to Google Doc {a.get('document_id')}"),
          label=lambda a: "Append to a Google Doc"),
        T("gsheets_read", "Read cells from a Google Sheet as text.", {"spreadsheet_id": s("Spreadsheet id"),
          "range": s("Range like A1:Z100 or Sheet1!A:D (default A1:Z100)")}, ["spreadsheet_id"], sheet_read, read=True,
          label=lambda a: "Read a Google Sheet"),
        T("gsheets_create", "Create a Google Sheet, optionally with its first rows.",
          {"title": s("Spreadsheet title"), "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                                                     "description": "First rows of cells"}},
          ["title"], sheet_create, risk=write_risk("write", lambda a: f"Create Google Sheet '{a.get('title')}'"),
          label=lambda a: f"Create Google Sheet {a.get('title')}"),
        T("gsheets_append", "Append one row of cells to a Google Sheet.",
          {"spreadsheet_id": s("Spreadsheet id"), "cells": {"type": "array", "items": {"type": "string"}},
           "range": s("Range, default Sheet1!A:Z")}, ["spreadsheet_id", "cells"], sheet_append,
          risk=write_risk("write", lambda a: f"Append a row to Google Sheet {a.get('spreadsheet_id')}"),
          label=lambda a: "Append a row to a Google Sheet"),
        T("gslides_create", "Create a Google Slides deck. Each slide is a title plus bullet lines (text-only).",
          {"title": s("Deck title"),
           "slides": {"type": "array", "items": {"type": "object", "properties": {
               "title": {"type": "string"}, "bullets": {"type": "array", "items": {"type": "string"}}}},
               "description": "Slides to add after the cover"}},
          ["title"], slide_create, risk=write_risk("write", lambda a: f"Create Google Slides deck '{a.get('title')}'"),
          label=lambda a: f"Create Google Slides {a.get('title')}"),
    ]


def _doc_fonts(family: str = "Calibri", serif: bool = False) -> dict:
    """Register a TTF for document rendering; Carlito (Calibri metrics) bundles with Job Hunt.
    Falls back to system fonts / built-in Helvetica so rendering never fails over fonts."""
    bundle = paths.resource_root() / "plugins" / "jobhunt" / "assets"
    sys_fonts = Path("/usr/share/fonts/truetype")
    win = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    fam = (family or "").lower()
    options: list[tuple[Path, Path, Path, Path]] = []
    if fam in ("times", "times new roman", "georgia", "garamond", "serif") or serif:
        options = [(win / "georgia.ttf", win / "georgiab.ttf", win / "georgiai.ttf", win / "georgiaz.ttf"),
                   (win / "times.ttf", win / "timesbd.ttf", win / "timesi.ttf", win / "timesbi.ttf"),
                   (sys_fonts / "liberation" / "LiberationSerif-Regular.ttf", sys_fonts / "liberation" / "LiberationSerif-Bold.ttf",
                    sys_fonts / "liberation" / "LiberationSerif-Italic.ttf", sys_fonts / "liberation" / "LiberationSerif-BoldItalic.ttf")]
    if fam in ("arial", "helvetica", "liberation"):
        options += [(win / "arial.ttf", win / "arialbd.ttf", win / "ariali.ttf", win / "arialbi.ttf"),
                    (sys_fonts / "liberation" / "LiberationSans-Regular.ttf", sys_fonts / "liberation" / "LiberationSans-Bold.ttf",
                     sys_fonts / "liberation" / "LiberationSans-Italic.ttf", sys_fonts / "liberation" / "LiberationSans-BoldItalic.ttf")]
    options += [(bundle / "Carlito-Regular.ttf", bundle / "Carlito-Bold.ttf", bundle / "Carlito-Italic.ttf", bundle / "Carlito-BoldItalic.ttf"),
                (sys_fonts / "crosextra" / "Carlito-Regular.ttf", sys_fonts / "crosextra" / "Carlito-Bold.ttf",
                 sys_fonts / "crosextra" / "Carlito-Italic.ttf", sys_fonts / "crosextra" / "Carlito-BoldItalic.ttf"),
                (sys_fonts / "liberation" / "LiberationSans-Regular.ttf", sys_fonts / "liberation" / "LiberationSans-Bold.ttf",
                 sys_fonts / "liberation" / "LiberationSans-Italic.ttf", sys_fonts / "liberation" / "LiberationSans-BoldItalic.ttf"),
                (sys_fonts / "dejavu" / "DejaVuSans.ttf", sys_fonts / "dejavu" / "DejaVuSans-Bold.ttf",
                 sys_fonts / "dejavu" / "DejaVuSans-Oblique.ttf", sys_fonts / "dejavu" / "DejaVuSans-BoldOblique.ttf")]
    fallback = {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique",
                "bolditalic": "Helvetica-BoldOblique", "family": "Helvetica", "mono": "Courier",
                "mono_bold": "Courier-Bold"}
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return fallback
    for paths_ in options:
        if all(p.is_file() for p in paths_):
            tag = re.sub(r"[^A-Za-z0-9]", "", fam)[:12] or "doc"
            names = (f"ZBD{tag}R", f"ZBD{tag}B", f"ZBD{tag}I", f"ZBD{tag}BI")
            try:
                for name, p in zip(names, paths_):
                    pdfmetrics.registerFont(TTFont(name, str(p)))
                pdfmetrics.registerFontFamily(f"ZBD{tag}", normal=names[0], bold=names[1],
                                              italic=names[2], boldItalic=names[3])
                return {"regular": names[0], "bold": names[1], "italic": names[2],
                        "bolditalic": names[3], "family": f"ZBD{tag}", "mono": "Courier", "mono_bold": "Courier-Bold"}
            except Exception:  # noqa: BLE001
                continue
    return fallback


def _inline_md(text: str) -> str:
    """Escape Paragraph-unsafe chars, then flesh out bold/italic/code."""
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
    esc = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", esc)
    esc = re.sub(r"`([^`\n]+)`", r'<font face="Courier" color="#334155">\1</font>', esc)
    esc = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<u><font color="#4338ca">\1</font></u> <font size="8" color="#64748b">[\2]</font>', esc)
    return esc


def _md_blocks(text: str) -> list[dict]:
    """Turn the model's markdown-lite into plain blocks for the PDF/DOCX renderers."""
    blocks: list[dict] = []
    code_buf: list[str] | None = None
    table_buf: list[list[str]] | None = None

    def flush_table():
        nonlocal table_buf
        if table_buf:
            block = {"kind": "table", "rows": table_buf}
            if block not in blocks:
                blocks.append(block)
        table_buf = None

    def flush_code():
        nonlocal code_buf
        if code_buf:
            blocks.append({"kind": "code", "text": "\n".join(code_buf).rstrip()})
        code_buf = None

    for raw in (text or "").split("\n"):
        line = raw.rstrip()
        if code_buf is not None:
            if line.strip() in ("```", "~~~"):
                flush_code()
            else:
                code_buf.append(raw)
            continue
        if line.strip().startswith(("```", "~~~")):
            flush_table()
            code_buf = []
            continue
        s = line.strip()
        if s and s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(not c or re.fullmatch(r":?-{1,}:?", c) for c in cells):
                continue  # markdown separator row (| --- | --- |)
            table_buf = (table_buf or [])
            table_buf.append(cells)
            continue
        flush_table()
        if not s:
            blocks.append({"kind": "gap"})
            continue
        m_h = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m_h:
            blocks.append({"kind": "h" + str(min(len(m_h.group(1)), 4)), "text": m_h.group(2).strip()})
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            blocks.append({"kind": "rule"})
            continue
        m_b = re.match(r"^([-*•])\s+(.*)$", s)
        if m_b:
            indent = len(raw) - len(raw.lstrip(" \t"))
            blocks.append({"kind": "bullet", "level": min(indent // 2, 2), "text": m_b.group(2).strip()})
            continue
        m_n = re.match(r"^(\d{1,2})[.)]\s+(.*)$", s)
        if m_n:
            blocks.append({"kind": "bullet", "level": 0, "text": m_n.group(2).strip(), "num": m_n.group(1)})
            continue
        if s.startswith("> "):
            blocks.append({"kind": "quote", "text": s[2:].strip()})
            continue
        blocks.append({"kind": "p", "text": s})
    flush_table()
    flush_code()
    return blocks


def _doc_page_size(paper: str):
    try:
        from reportlab.lib.pagesizes import A4, letter
        return A4 if ((paper or "a4").lower() == "a4") else letter
    except ImportError:
        return (595.27, 841.89)


def _pdf_render(fp: Path, title: str, text: str, a: dict) -> list[str]:
    """Render markdown-lite text as a properly typeset PDF. Returns summary lines."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import (HRFlowable, Paragraph, Preformatted,
                                        SimpleDocTemplate, Spacer, Table, TableStyle)
    except ImportError:
        raise ConnectorError("reportlab is not installed on the server.")

    fonts = _doc_fonts(a.get("font") or "Calibri", serif=(a.get("font") or "").lower() in ("times", "georgia", "serif"))
    accent = a["accent"] if isinstance(a.get("accent"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", a["accent"]) else "#2563eb"
    slate, muted = HexColor("#0f172a"), HexColor("#475569")
    body = ParagraphStyle("body", fontName=fonts["regular"], fontSize=10.5, leading=15.6,
                          textColor=slate, alignment=TA_LEFT, spaceAfter=5)
    pstyles = {
        "h1": ParagraphStyle("h1", parent=body, fontName=fonts["bold"], fontSize=15.5, leading=20, spaceBefore=13, spaceAfter=7),
        "h2": ParagraphStyle("h2", parent=body, fontName=fonts["bold"], fontSize=12.8, leading=16.5, spaceBefore=10, spaceAfter=5),
        "h3": ParagraphStyle("h3", parent=body, fontName=fonts["bold"], fontSize=11.3, leading=15, spaceBefore=9, spaceAfter=4),
        "p": body,
        "quote": ParagraphStyle("quote", parent=body, fontName=fonts["italic"], textColor=muted, leftIndent=10),
        "pre": ParagraphStyle("pre", fontName=fonts.get("mono", "Courier"), fontSize=8.8, leading=12, textColor=HexColor("#1e293b")),
        "cell": ParagraphStyle("cell", parent=body, fontSize=9.4, leading=12.6, spaceAfter=0),
        "cellh": ParagraphStyle("cellh", parent=body, fontSize=9.4, leading=12.6, spaceAfter=0, fontName=fonts["bold"]),
        "title": ParagraphStyle("title", parent=body, fontName=fonts["bold"], fontSize=19, leading=25, spaceAfter=3),
        "title_sub": ParagraphStyle("title_sub", parent=body, fontSize=8.6, leading=12, textColor=muted, spaceAfter=8),
    }
    blocks = _md_blocks(text)
    lead_h1 = next((b for b in blocks if b["kind"] == "h1"), None)
    if lead_h1 and lead_h1["text"].strip() == title.strip():
        blocks = [b for b in blocks if b is not lead_h1]
    blocks = [b for b in blocks if not (b["kind"] == "gap" and blocks and blocks[0] is b)]

    story: list = []
    story.append(Paragraph(_inline_md(title or "Document"), pstyles["title"]))
    from datetime import datetime as _dt
    story.append(Paragraph(_dt.now().strftime("%d %B %Y"), pstyles["title_sub"]))
    story.append(HRFlowable(width="100%", thickness=1.1, color=HexColor(accent), spaceBefore=2, spaceAfter=10))
    prev_gap = True
    for b in blocks:
        k = b["kind"]
        if k == "gap":
            if not prev_gap:
                story.append(Spacer(1, 6))
            prev_gap = True
            continue
        prev_gap = False
        if k in ("h1", "h2", "h3"):
            story.append(Paragraph(_inline_md(b.get("text", "")), pstyles[k]))
        elif k == "p":
            story.append(Paragraph(_inline_md(b.get("text", "")), pstyles["p"]))
        elif k == "quote":
            story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#e2e8f0")))
            story.append(Paragraph(_inline_md(b.get("text", "")), pstyles["quote"]))
            story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#e2e8f0")))
            story.append(Spacer(1, 4))
        elif k == "rule":
            story.append(HRFlowable(width="100%", thickness=0.8, color=HexColor("#e2e8f0"), spaceBefore=6, spaceAfter=6))
            prev_gap = True
        elif k == "code":
            box = Table([[Preformatted(b.get("text", ""), pstyles["pre"])]], colWidths=["100%"])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor("#f8fafc")),
                                     ("BOX", (0, 0), (-1, -1), 0.6, HexColor("#e2e8f0")),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                     ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
            story.append(box)
            story.append(Spacer(1, 6))
            prev_gap = True
        elif k == "bullet":
            mark = f"{b.get('num')}." if b.get("num") else "•"
            st = pstyles["bullet"] = pstyles.get("bullet") or ParagraphStyle(
                "bullet", parent=body, leftIndent=16, bulletIndent=4)
            if b.get("level"):
                st = ParagraphStyle(f"bl{b['level']}", parent=st,
                                    leftIndent=16 + 16 * b["level"], bulletIndent=4 + 16 * b["level"])
            story.append(Paragraph(f'<bullet color="{accent}">{mark}</bullet>' + _inline_md(b.get("text", "")), st))
            story.append(Spacer(1, 1.5))
        elif k == "table":
            rows = [r for r in (b.get("rows") or []) if r]
            if not rows:
                continue
            ncols = max(len(r) for r in rows)
            data = []
            for ri, row in enumerate(rows):
                style = pstyles["cellh"] if ri == 0 else pstyles["cell"]
                padded = list(row) + [""] * (ncols - len(row))
                data.append([Paragraph(_inline_md(c), style) for c in padded])
            width = _doc_page_size(a.get("paper") or "a4")[0] - 116
            t = Table(data, colWidths=[width / ncols] * ncols, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#f1f5f9")),
                ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#e2e8f0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, HexColor("#fbfcfe")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(t)
            story.append(Spacer(1, 8))
            prev_gap = True

    from reportlab.pdfgen import canvas as _canvas

    def _footer(can, _doc):
        can.saveState()
        w_page, _h_page = can._pagesize
        can.setFillColor(HexColor("#94a3b8"))
        try:
            can.setFont(fonts["regular"], 8)
        except Exception:  # noqa: BLE001
            pass
        can.drawString(58, 30, (title or "Document")[:70])
        can.drawRightString(w_page - 58, 30, f"Page {can.getPageNumber()}")
        can.setFillColor(HexColor(accent))
        can.rect(58, 40, w_page - 116, 0.7, stroke=0, fill=1)
        can.restoreState()

    doc = SimpleDocTemplate(str(fp), pagesize=_doc_page_size(a.get("paper") or "a4"),
                            leftMargin=58, rightMargin=58, topMargin=64, bottomMargin=56,
                            title=(title or "Document")[:120], author="ZerotheBot")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    n = 1
    try:
        from pypdf import PdfReader
        n = len(PdfReader(str(fp)).pages)
    except Exception:  # noqa: BLE001
        pass
    return [f"Created PDF {fp.name} ({n} page{'s' if n != 1 else ''})."]


def _split_inline(text: str):
    """Split raw text into (chunk, bold, italic, mono) for DOCX runs."""
    out = []
    pos = 0
    pattern = re.compile(r"\*\*(.+?)\*\*|(?<!\*)\*([^*\n]+)\*(?!\*)|`([^`\n]+)`")
    for m in pattern.finditer(text or ""):
        if m.start() > pos:
            out.append((text[pos:m.start()], False, False, False))
        if m.group(1) is not None:
            out.append((m.group(1), True, False, False))
        elif m.group(2) is not None:
            out.append((m.group(2), False, True, False))
        else:
            out.append((m.group(3), False, False, True))
        pos = m.end()
    if pos < len(text or ""):
        out.append((text[pos:], False, False, False))
    return out


def _docx_runs(paragraph, text: str, fam: str, mono="Courier New"):
    """Append raw text with **bold** / *italic* / `code` as real DOCX runs."""
    for chunk, bold, italic, mono_on in _split_inline(text):
        if not chunk:
            continue
        r = paragraph.add_run(chunk)
        r.font.name = mono if mono_on else fam
        r.bold = bold
        r.italic = italic


def _docx_border(paragraph, color: str, size: int = 8, side: str = "bottom"):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    edge = OxmlElement(f"w:{side}")
    edge.set(qn("w:val"), "single")
    edge.set(qn("w:sz"), str(size))
    edge.set(qn("w:space"), "2")
    edge.set(qn("w:color"), color)
    pBdr.append(edge)
    pPr.append(pBdr)


def _docx_render(fp: Path, title: str, text: str, a: dict) -> list[str]:
    try:
        from docx import Document
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt, RGBColor
    except ImportError:
        raise ConnectorError("python-docx is not installed on the server.")
    fonts = _doc_fonts(a.get("font") or "Calibri", serif=(a.get("font") or "").lower() in ("times", "georgia", "serif"))
    fam = fonts["family"]
    accent = a["accent"] if isinstance(a.get("accent"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", a["accent"]) else "#2563eb"
    hex6 = accent.lstrip("#").upper()
    doc = Document()
    for section in doc.sections:
        section.top_margin = Pt(58); section.bottom_margin = Pt(58)
        section.left_margin = Pt(58); section.right_margin = Pt(58)
    normal = doc.styles["Normal"]
    normal.font.name = fam
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(5)

    from datetime import datetime as _dt
    t = doc.add_paragraph()
    t.paragraph_format.space_after = Pt(1)
    r = t.add_run(title or "Document")
    r.font.name = fam; r.font.size = Pt(19); r.font.bold = True
    r.font.color.rgb = RGBColor.from_string("0f172a")
    sub = doc.add_paragraph()
    sub.paragraph_format.space_after = Pt(4)
    r = sub.add_run(_dt.now().strftime("%d %B %Y"))
    r.font.name = fam; r.font.size = Pt(8.6)
    r.font.color.rgb = RGBColor.from_string("64748b")
    rule = doc.add_paragraph()
    rule.paragraph_format.space_after = Pt(10)
    rule.paragraph_format.space_before = Pt(0)
    r = rule.add_run("")
    _docx_border(rule, hex6)

    blocks = _md_blocks(text)
    for b in blocks:
        k = b["kind"]
        if k == "gap":
            continue
        if k in ("h1", "h2", "h3"):
            p = doc.add_paragraph()
            lvl = int(k[1])
            size = {1: 15.5, 2: 12.8, 3: 11.3}.get(lvl, 11.3)
            r = p.add_run(b.get("text", ""))
            r.font.name = fam; r.font.size = Pt(size); r.font.bold = True
            r.font.color.rgb = RGBColor.from_string("0f172a")
            p.paragraph_format.space_before = Pt(12 if lvl == 1 else 9)
            p.paragraph_format.space_after = Pt(5)
        elif k == "p":
            p = doc.add_paragraph()
            _docx_runs(p, b.get("text", ""), fam)
        elif k == "bullet":
            p = doc.add_paragraph(style="List Bullet")
            _docx_runs(p, b.get("text", ""), fam)
            p.paragraph_format.left_indent = Pt(16 + 16 * (b.get("level") or 0))
            p.paragraph_format.space_after = Pt(2)
        elif k == "quote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(10)
            _docx_runs(p, b.get("text", ""), fam)
            for r in p.runs:
                r.italic = True
                r.font.color.rgb = RGBColor.from_string("475569")
        elif k == "rule":
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.add_run("")
            _docx_border(p, "e2e8f0", size=4)
        elif k == "code":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(10)
            p.paragraph_format.space_after = Pt(6)
            r = p.add_run(b.get("text", ""))
            r.font.name = "Courier New"; r.font.size = Pt(8.8)
        elif k == "table":
            rows = [r_ for r_ in (b.get("rows") or []) if r_]
            if not rows:
                continue
            ncol = max(len(r_) for r_ in rows)
            table = doc.add_table(rows=len(rows), cols=ncol)
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for ri, row in enumerate(rows):
                for ci in range(ncol):
                    cell = table.cell(ri, ci)
                    cp = cell.paragraphs[0]
                    r = cp.add_run(row[ci] if ci < len(row) else "")
                    r.font.size = Pt(9.4)
                    r.font.name = fam
                    if ri == 0:
                        r.font.bold = True
            doc.add_paragraph()
    doc.save(str(fp))
    return [f"Created DOCX {fp.name}."]

def build_gdrive(c: Conn) -> list[ToolSpec]:
    """Google Drive: search, read, upload, share and organize the user's files."""
    DRIVE = "https://www.googleapis.com/drive/v3/files"
    UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
    _GOOGLE_MIME_EXPORT = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    def H() -> dict:
        return c.google_headers()

    def search(ctx, a):
        clauses = ["trashed=false"]
        if a.get("name"):
            clauses.append("name contains '" + str(a["name"]).replace("'", "\\'") + "'")
        if a.get("folder_id"):
            clauses.append(f"'{a['folder_id']}' in parents")
        if a.get("mime_type"):
            clauses.append(f"mimeType='{a['mime_type']}'")
        res = c.http(ctx, "GET", DRIVE, headers=H(),
                     params={"q": " and ".join(clauses), "pageSize": min(int(a.get("limit") or 20), 50),
                             "orderBy": "modifiedTime desc",
                             "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink)"})
        out = [{"id": f["id"], "name": f["name"], "type": f["mimeType"],
                "size": f.get("size"), "modified": f.get("modifiedTime"), "url": f.get("webViewLink")}
               for f in res.get("files", [])]
        return out or "No matching files found in Google Drive."

    def read(ctx, a):
        meta = c.http(ctx, "GET", f"{DRIVE}/{a['file_id']}", headers=H(),
                      params={"fields": "name,mimeType"})
        mime = meta.get("mimeType", "")
        if mime in _GOOGLE_MIME_EXPORT:
            content = c.http(ctx, "GET", f"{DRIVE}/{a['file_id']}/export", headers=H(),
                             params={"mimeType": _GOOGLE_MIME_EXPORT[mime]})
        else:
            content = c.http(ctx, "GET", f"{DRIVE}/{a['file_id']}", headers=H(),
                             params={"alt": "media"})
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if len(text) > 20000:
            text = text[:20000] + f"\n[... truncated {len(text) - 20000} chars ...]"
        return {"name": meta.get("name"), "type": mime, "content": text or "(empty file)"}

    def upload(ctx, a):
        meta = c.http(ctx, "POST", UPLOAD, headers=H(),
                      params={"uploadType": "media", "fields": "id,name,webViewLink"},
                      data=str(a.get("content") or ""))
        # media upload names the file "Untitled"; rename via metadata patch
        name = str(a.get("name") or "uploaded.txt").strip()[:100]
        res = c.http(ctx, "PATCH", f"{DRIVE}/{meta['id']}", headers=H(),
                     json_body={"name": name}, params={"fields": "id,name,webViewLink"})
        return {"file_id": res["id"], "name": res["name"], "url": res.get("webViewLink")}

    def create_folder(ctx, a):
        res = c.http(ctx, "POST", DRIVE, headers=H(),
                     json_body={"name": str(a["name"]).strip()[:100],
                                "mimeType": "application/vnd.google-apps.folder"},
                     params={"fields": "id,name,webViewLink"})
        return {"folder_id": res["id"], "name": res["name"], "url": res.get("webViewLink")}

    def share(ctx, a):
        role = a.get("role") if a.get("role") in ("reader", "writer") else "reader"
        body: dict = {"role": role, "type": "anyone"} if not a.get("email") else \
            {"role": role, "type": "user", "emailAddress": str(a["email"])}
        c.http(ctx, "POST", f"{DRIVE}/{a['file_id']}/permissions", headers=H(), json_body=body)
        return f"Shared '{a['file_id']}' with {'anyone with the link' if not a.get('email') else a['email']} ({role})."

    def trash(ctx, a):
        c.http(ctx, "PATCH", f"{DRIVE}/{a['file_id']}", headers=H(), json_body={"trashed": True})
        return f"Moved file {a['file_id']} to the Drive trash (recoverable)."

    return [
        T("drive_search", "Search the user's Google Drive for files by name, folder or type.",
          {"name": s("File name fragment to search for"), "folder_id": s("Optional parent folder id"),
           "mime_type": s("Optional exact MIME type filter"), "limit": s("Max results (default 20)")},
          [], search, read=True,
          label=lambda a: "Drive search: " + short(a.get("name") or "all files", 40)),
        T("drive_read", "Read the text content of a Google Drive file (Google Docs are exported as text).",
          {"file_id": s("The Drive file id")}, ["file_id"], read, read=True,
          label=lambda a: "Drive read: " + short(a.get("file_id"), 30)),
        T("drive_upload", "Create a new text file (txt/csv/md/json/code) in Google Drive.",
          {"name": s("File name, e.g. report.csv"), "content": s("File content as text")},
          ["name", "content"], upload,
          label=lambda a: "Drive upload: " + short(a.get("name"), 40)),
        T("drive_create_folder", "Create a folder in Google Drive.",
          {"name": s("Folder name")}, ["name"], create_folder,
          label=lambda a: "Drive create folder: " + short(a.get("name"), 40)),
        T("drive_share", "Share a Drive file (with a specific person or anyone with the link).",
          {"file_id": s("The Drive file id"), "email": s("Email to share with (omit for link sharing)"),
           "role": s("reader or writer (default reader)")},
          ["file_id"], share,
          label=lambda a: "Drive share: " + short(a.get("file_id"), 30)),
        T("drive_delete", "Move a Drive file to the trash (recoverable, not a hard delete).",
          {"file_id": s("The Drive file id")}, ["file_id"], trash,
          label=lambda a: "Drive trash: " + short(a.get("file_id"), 30)),
    ]


def build_documents(c: Conn) -> list[ToolSpec]:
    """Read and create PDF and DOCX files in the workspace. Documents render with real
    typography (headings, bullets, tables, page numbers) from markdown-lite text."""

    def _ws_path(a, key="path"):
        rel = (a.get(key) or "").strip()
        if not rel:
            raise ConnectorError("path is required.")
        full, inside = c.mgr.engine.computer.resolve(rel)
        if not inside:
            raise ConnectorError("That path is outside the workspace.")
        return full

    def pdf_read(ctx, a):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ConnectorError("pypdf is not installed on the server.")
        fp = _ws_path(a)
        if not fp.is_file():
            raise ConnectorError(f"No such file: {fp}")
        if fp.suffix.lower() != ".pdf":
            raise ConnectorError("Not a PDF file.")
        reader = PdfReader(str(fp))
        out = []
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            out.append(f"--- page {i + 1} ---\n{t}")
        text = "\n".join(out)
        return text[:200000] or "No text extracted."

    def docx_read(ctx, a):
        try:
            from docx import Document
        except ImportError:
            raise ConnectorError("python-docx is not installed on the server.")
        fp = _ws_path(a)
        if not fp.is_file():
            raise ConnectorError(f"No such file: {fp}")
        if fp.suffix.lower() != ".docx":
            raise ConnectorError("Not a DOCX file.")
        doc = Document(str(fp))
        out = []
        for para in doc.paragraphs:
            if para.text.strip():
                out.append(para.text)
        for table in doc.tables:
            for row in table.rows:
                out.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(out)
        return text[:200000] or "No text extracted."

    def pdf_create(ctx, a):
        fp = _ws_path(a)
        title = (a.get("title") or "Document").strip() or "Document"
        text = a.get("text") or ""
        if not text.strip():
            raise ConnectorError("text is required.")
        lines = _pdf_render(fp, title, text, a)
        rel = c.mgr.engine.computer.rel(fp)
        size = fp.stat().st_size
        return ToolResult("\n".join(lines) + f" It opens with a styled preview in chat.",
                          files=[{"path": rel, "name": fp.name, "size": size, "mime": "application/pdf"}])

    def docx_create(ctx, a):
        fp = _ws_path(a)
        title = (a.get("title") or "Document").strip() or "Document"
        text = a.get("text") or ""
        if not text.strip():
            raise ConnectorError("text is required.")
        lines = _docx_render(fp, title, text, a)
        rel = c.mgr.engine.computer.rel(fp)
        size = fp.stat().st_size
        return ToolResult("\n".join(lines),
                          files=[{"path": rel, "name": fp.name, "size": size,
                                  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}])

    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
            n /= 1024
        return f"{n:.2f} TB"

    def file_attach(ctx, a):
        """Attach an existing workspace file to the bot's reply so the user can view/download it in chat."""
        fp = _ws_path(a)
        if not fp.is_file():
            raise ConnectorError(f"No such file: {fp}")
        st = fp.stat()
        mime = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        rel = c.mgr.engine.computer.rel(fp)
        return ToolResult(
            text=f"Attached {fp.name} ({_fmt_size(st.st_size)}).",
            files=[{"path": rel, "name": fp.name, "size": st.st_size, "mime": mime}],
        )

    def read_file(ctx, a):
        """Read a workspace file: PDF/DOCX/TXT/CSV return extracted text; images return format/size/mode."""
        fp = _ws_path(a)
        if not fp.is_file():
            raise ConnectorError(f"No such file: {fp}")
        ext = fp.suffix.lower()
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                raise ConnectorError("pypdf is not installed on the server.")
            reader = PdfReader(str(fp))
            parts = []
            for i, page in enumerate(reader.pages):
                t = page.extract_text() or ""
                parts.append(f"--- page {i + 1} ---\n{t}")
            return "\n".join(parts)[:200000] or "No text extracted."
        if ext == ".docx":
            try:
                from docx import Document
            except ImportError:
                raise ConnectorError("python-docx is not installed on the server.")
            doc = Document(str(fp))
            out = []
            for para in doc.paragraphs:
                if para.text.strip():
                    out.append(para.text)
            for table in doc.tables:
                for row in table.rows:
                    out.append(" | ".join(cell.text for cell in row.cells))
            return "\n".join(out)[:200000] or "No text extracted."
        if ext in (".txt", ".csv", ".md", ".json", ".py", ".html", ".xml", ".yaml", ".yml", ".log"):
            return fp.read_text(encoding="utf-8", errors="replace")[:200000] or "(empty file)"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            try:
                from PIL import Image
            except ImportError:
                raise ConnectorError("Pillow (PIL) is not installed on the server.")
            with Image.open(fp) as im:
                return (f"Image: {fp.name}\n"
                        f"Format: {im.format}\nMode: {im.mode}\nSize: {im.size[0]}x{im.size[1]}\n"
                        f"File size: {_fmt_size(fp.stat().st_size)}")
        raise ConnectorError(f"Cannot read {ext} files. Supported: PDF, DOCX, TXT, CSV, PNG, JPG, GIF, WEBP.")

    return [
        T("pdf_read", "Extract text from a PDF file in the workspace.",
          {"path": s("Workspace path to the PDF file")}, ["path"], pdf_read, read=True,
          label=lambda a: "Read a PDF"),
        T("docx_read", "Extract text from a Word (.docx) file in the workspace.",
          {"path": s("Workspace path to the DOCX file")}, ["path"], docx_read, read=True,
          label=lambda a: "Read a Word document"),
        T("pdf_create", "Create a PDF document from markdown-lite text: # headings, - bullets, 1. numbered lists, "
                        "| tables |, > quotes, code blocks, **bold** / `code`. Renders with real typography - title, "
                        "styles, page numbers - and previews in chat.",
          {"path": s("Workspace path to save the PDF (e.g. shared/report.pdf)"),
           "title": s("Document title"), "text": s("Markdown-lite body (#, ##, -, 1., | tables |, >, ```)"),
           "paper": s("Paper size: A4 (default) or Letter"),
           "font": s("Font: Calibri (default), Arial, Georgia or 'serif'"),
           "accent": s("Accent color hex like #2563eb (optional)")},
          ["path", "text"], pdf_create,
          risk=R("write", lambda a: f"Create PDF {a.get('path')}", lambda a: a.get('path') or "file"),
          label=lambda a: f"Create PDF {a.get('path')}"),
        T("docx_create", "Create a Word (.docx) document from markdown-lite text (same model as pdf_create).",
          {"path": s("Workspace path to save the DOCX (e.g. shared/report.docx)"),
           "title": s("Document title"), "text": s("Markdown-lite body (#, ##, -, 1., | tables |, >, ```)"),
           "font": s("Font name for Word (default Calibri)")},
          ["path", "text"], docx_create,
          risk=R("write", lambda a: f"Create DOCX {a.get('path')}", lambda a: a.get('path') or "file"),
          label=lambda a: f"Create DOCX {a.get('path')}"),
        T("file_attach", "Attach an existing workspace file to your reply so the user can view or download it in chat.",
          {"path": s("Workspace path to the file (e.g. shared/report.pdf, shared/chart.png)")},
          ["path"], file_attach,
          risk=R("read", lambda a: f"Attach {a.get('path')}", lambda a: a.get('path') or "file"),
          label=lambda a: f"Attach {a.get('path')}"),
        T("read_file", "Read a file the user uploaded or a bot created: PDF, DOCX, TXT, CSV, or image. Returns extracted text (PDF/DOCX/TXT/CSV) or image info (format, size, mode).",
          {"path": s("Workspace path to the file (e.g. shared/uploads/document.pdf, shared/uploads/photo.png)")},
          ["path"], read_file,
          risk=R("read", lambda a: f"Read {a.get('path')}", lambda a: a.get('path') or "file"),
          label=lambda a: f"Read {a.get('path')}"),
        T("python_run", "Run Python code with access to pandas, openpyxl, pdfplumber, python-pptx, docxtpl, beautifulsoup4, html2text, markdown, pypdf, python-docx, reportlab and more. Code runs in the Bot workspace. Use this for data analysis, report generation, file processing, and anything the other tools don't cover.",
          {"code": s("Python code to run"), "timeout": i("Timeout in seconds (default 60, max 300)")},
          ["code"], python_run,
          risk=R("execute", lambda a: f"Run Python code ({len(a.get('code', ''))} chars)", lambda a: "python"),
          label=lambda a: "Run Python code"),
    ]


def python_run(ctx, a):
    """Run Python code in a subprocess with access to all installed libraries."""
    code = a.get("code", "").strip()
    if not code:
        raise ConnectorError("code is required.")
    try:
        timeout = min(int(a.get("timeout", 60) or 60), 300)
    except (TypeError, ValueError):
        timeout = 60
    if timeout < 1:
        timeout = 1
    full, inside = ctx.engine.computer.resolve(".")
    if not inside or not full or not full.exists():
        raise ConnectorError("Cannot resolve workspace directory.")
    script = textwrap.dedent(code)
    script_path = full / "_zb_python_run.py"
    script_path.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["python3", "_zb_python_run.py"],
            cwd=str(full),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        result = f"exit code: {proc.returncode}\n"
        if out:
            result += f"--- stdout ---\n{out[:50000]}\n"
        if err:
            result += f"--- stderr ---\n{err[:10000]}\n"
        if proc.returncode != 0:
            raise ConnectorError(result)
        return result or "Script ran successfully with no output."
    except subprocess.TimeoutExpired:
        raise ConnectorError(f"Python script timed out after {timeout} seconds.")
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def build_shell(c: Conn) -> list[ToolSpec]:
    """Direct host shell for privileged Bots: full control of the machine the service
    runs on — the same power an engineer has (git, pip, service restarts, file edits)."""

    def run_command(ctx, a):
        """Run a shell command on the host server (Linux VM that runs ZerotheBot)."""
        cmd = (a.get("cmd") or "").strip()
        if not cmd:
            raise ConnectorError("cmd is required.")
        try:
            timeout = min(int(a.get("timeout", 120) or 120), 600)
        except (TypeError, ValueError):
            timeout = 120
        if timeout < 1:
            timeout = 1
        cwd = (a.get("cwd") or "").strip()
        if cwd:
            full, inside = c.mgr.engine.computer.resolve(cwd)
            if not inside:
                raise ConnectorError("cwd must be inside the workspace.")
        else:
            full, _inside = c.mgr.engine.computer.resolve(".")
        env = {k: v for k, v in os.environ.items() if "SECRET" not in k.upper() and "TOKEN" not in k.upper() and not k.startswith("GOOGLE_")}
        env.setdefault("PYTHONUTF8", "1")
        try:
            proc = subprocess.run(["bash", "-lc", cmd], cwd=str(full), capture_output=True, text=True,
                                  timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ConnectorError(f"Command timed out after {timeout}s and was stopped.")
        except FileNotFoundError:
            raise ConnectorError("bash is not available on this host.")
        out = (proc.stdout or "")
        err = (proc.stderr or "")
        result = f"exit code: {proc.returncode}\n"
        if out:
            result += f"--- stdout ---\n{out[:40000]}\n"
        if err:
            result += f"--- stderr ---\n{err[:12000]}\n"
        return result or "(no output)"

    return [
        T("run_command", "Run a shell command on the host server that runs ZerotheBot (the machine with the app's code and service). "
                         "Full Linux access: git, pip, python, curl, systemctl-like restarts, file edits. cwd is a workspace-relative folder "
                         "(optional; defaults to the Bot's workspace). Use for fixing, building, and inspecting the app itself. "
                         "Long builds should be started with nohup in the background and polled.",
          {"cmd": {"type": "string", "description": "The shell command to run, e.g. 'git status' (required). "
                                                    "Separate steps with && or ; . Redirect long output to a file in workspace and read it.",
                   },
           "timeout": {"type": "integer", "description": "Seconds to allow (max 600, default 120)."},
           "cwd": {"type": "string", "description": "Optional workspace-relative working directory."}},
          ["cmd"], run_command,
          risk=lambda ctx, a: Risk("command", f"Run command: {short(a.get('cmd', ''), 140)}",
                                  {"pattern": ((a.get("cmd") or "").strip().split() or ["*"])[0]}),
          label=lambda a: f"Run command: {short(a.get('cmd', ''), 80)}"),
    ]


def _bump_app_version(repo: str) -> tuple[str, str, int] | None:
    """Increment rn-app/app.json patch version + versionCode so a rebuild installs as an update.
    Returns (old_version, new_version, new_code) or None when the file is missing/invalid."""
    path = Path(repo) / "rn-app" / "app.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expo = data.setdefault("expo", {})
        old = str(expo.get("version") or "0.0.0")
        parts = [int(x) for x in re.findall(r"\d+", old)[:3]]
        while len(parts) < 3:
            parts.append(0)
        parts[2] += 1
        new = ".".join(str(p) for p in parts)
        expo["version"] = new
        android = expo.setdefault("android", {})
        code = int(android.get("versionCode") or 1) + 1
        android["versionCode"] = code
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return old, new, code
    except Exception:  # noqa: BLE001
        return None


def build_release(c: Conn) -> list[ToolSpec]:
    """Ship the app from inside a Bot turn: build the APK, publish it for download,
    restart the service on the new code, and push commits to GitHub.

    Builds are detached (setsid nohup) because an APK build outlives a tool call;
    deploy is detached with a short delay because restarting the service kills the
    process that is answering this tool call.
    """
    repo = (c.cfg("repo_path") or "/data/home/zerothebot").strip().rstrip("/")
    apk_base = (c.cfg("apk_base") or "https://8888-agquljzsz7k42vgoenj7qr6fim.instabox.tech").strip().rstrip("/")
    token = (c.cfg("github_token") or "").strip()
    build_log = "/tmp/zb-build.log"
    build_script = "/tmp/zb-build.sh"
    deploy_log = "/tmp/zb-deploy.log"

    def _sh(cmd: str, timeout: int = 120, extra_env: dict | None = None, redact: tuple = ()):
        env = dict(os.environ)
        env.pop("GIT_ASKPASS", None)
        if extra_env:
            env.update(extra_env)
        try:
            p = subprocess.run(["bash", "-lc", cmd], cwd=repo, capture_output=True, text=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ConnectorError(f"That took longer than {timeout}s and was stopped.")
        except FileNotFoundError:
            raise ConnectorError("bash is not available on this host.")
        out = (p.stdout or "")
        if p.stderr:
            out += ("\n" if out else "") + p.stderr
        for s in redact:
            if s:
                out = out.replace(s, "***")
        return p.returncode, out.strip()

    def _building() -> tuple[bool, str]:
        _rc, out = _sh("pgrep -af 'zb-build[.]sh|gradlew assembleRelease' | grep -v pgrep || true", 20)
        return bool(out.strip()), out.strip()

    def _manifest() -> dict | None:
        try:
            return json.loads(Path("/tmp/apkserve/update.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        _rc, out = _sh(f"curl -s --max-time 8 {shlex.quote(apk_base + '/update.json')}", 20)
        try:
            return json.loads(out)
        except Exception:  # noqa: BLE001
            return None

    def _tail(path: str, lines: int = 40) -> str:
        _rc, out = _sh(f"tail -n {int(lines)} {shlex.quote(path)} 2>/dev/null || true", 20)
        return out

    def release_status(ctx, a):
        """Report the repo, a running build, the published APK and the service."""
        parts: list[str] = []
        _rc, log = _sh("git log --oneline -3", 20)
        parts.append(f"Repo: {repo}\n{log or '(no commits)'}")
        _rc, dirty = _sh("git status --porcelain | head -20", 20)
        parts.append("Uncommitted changes: " + ("none" if not dirty else "\n" + dirty))
        busy, procs = _building()
        parts.append("APK build running: " + (f"yes\n{procs}" if busy else "no"))
        m = _manifest()
        if m:
            parts.append(f"Published APK: {m.get('version')} (code {m.get('code')}), "
                         f"sha256 {str(m.get('sha256', ''))[:16]}...\nDownload: {m.get('apk')}")
        if busy:
            parts.append("Build log tail:\n" + (_tail(build_log, 12) or "(no output yet)"))
        _rc, svc = _sh("curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8765/ || true", 20)
        parts.append("Service HTTP: " + (svc or "no answer"))
        return "\n\n".join(parts)

    def release_build_apk(ctx, a):
        """Start a detached APK build and poll it for a while."""
        busy, procs = _building()
        if busy:
            return ("An APK build is already running, so I did not start another.\n" + procs +
                    "\nUse release_status to watch it.")
        try:
            wait = min(max(int(a.get("wait", 90) or 90), 5), 540)
        except (TypeError, ValueError):
            wait = 90
        bumped = None
        if a.get("bump", True):
            bumped = _bump_app_version(repo)
        script = f"""#!/usr/bin/env bash
cd {shlex.quote(repo + '/rn-app')} || {{ echo "__ZB_BUILD_EXIT:90"; exit 90; }}
bash build.sh
rc=$?
echo "__ZB_BUILD_EXIT:$rc"
if [ "$rc" -eq 0 ]; then
  bash publish.sh
  echo "__ZB_PUBLISH_EXIT:$?"
fi
"""
        try:
            Path(build_script).write_text(script, encoding="utf-8")
        except OSError as exc:
            raise ConnectorError(f"Could not write the build script: {exc}")
        _sh(f"rm -f {build_log}; setsid nohup bash {build_script} > {build_log} 2>&1 < /dev/null & echo started", 30)
        end = time.time() + wait
        log = ""
        while time.time() < end:
            time.sleep(3)
            log = _tail(build_log, 200)
            if "__ZB_BUILD_EXIT:" in log:
                break
        log = _tail(build_log, 200)
        m = re.search(r"__ZB_BUILD_EXIT:(\d+)", log)
        bump_note = ""
        if bumped:
            bump_note = (f"\nVersion bumped {bumped[0]} -> {bumped[1]} (versionCode {bumped[2]}) in rn-app/app.json. "
                         "The bump is committed locally; release_push publishes it.\n")
        if not m:
            return ("The APK build is running in the background. It usually takes 6-20 minutes.\n"
                    "Ask me to check with release_status, or call release_status yourself.\n"
                    + bump_note + "\nLatest build output:\n" + _tail(build_log, 20))
        build_rc = int(m.group(1))
        if build_rc != 0:
            raise ConnectorError(f"The APK build failed (exit {build_rc}).\n\nLast lines:\n" + _tail(build_log, 40))
        pm = re.search(r"__ZB_PUBLISH_EXIT:(\d+)", log)
        if pm and int(pm.group(1)) != 0:
            raise ConnectorError(f"The APK built but publishing failed (exit {pm.group(1)}).\n\n"
                                 + _tail(build_log, 40))
        man = _manifest()
        if not man:
            return "The APK built and published, but I could not read update.json.\n\n" + _tail(build_log, 20)
        if bumped:
            _sh(f'git -c user.name="ZerotheBot" -c user.email="bot@zerothebot.local" '
                f'commit -m "release: {bumped[1]} (code {bumped[2]})" -- rn-app/app.json', 60)
        return (f"APK built and published: {man.get('version')} (code {man.get('code')})\n"
                f"Download: {man.get('apk')}\nsha256: {man.get('sha256')}\n\n"
                "Android installs it over the old version (same signing key).\n"
                + bump_note +
                "\nBuild tail:\n" + _tail(build_log, 12))

    def release_deploy(ctx, a):
        """Reset the VM to origin/main and restart the service (detached)."""
        reset = bool(a.get("reset", True))
        branch = (a.get("branch") or "main").strip() or "main"
        lines = [
            "#!/usr/bin/env bash",
            "sleep 12",
            f"cd {shlex.quote(repo)} || exit 1",
        ]
        if reset:
            lines += [f"git fetch origin {shlex.quote(branch)} --quiet",
                      f"git reset --hard origin/{shlex.quote(branch)} --quiet"]
        lines += [
            "pkill -f 'main[.]py --service' || true",
            "sleep 3",
            "PORT=8765 ZEROTHEBOT_HOST=0.0.0.0 PLAYWRIGHT_BROWSERS_PATH=/data/home/.cache/ms-playwright "
            "setsid nohup bash run-host.sh > /tmp/zb-service.log 2>&1 < /dev/null &",
        ]
        try:
            Path("/tmp/zb-deploy.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            raise ConnectorError(f"Could not write the deploy script: {exc}")
        _sh(f"rm -f {deploy_log}; setsid nohup bash /tmp/zb-deploy.sh > {deploy_log} 2>&1 < /dev/null & echo scheduled", 30)
        _rc, head = _sh(f"git rev-parse --short HEAD", 20)
        return (f"Deploy scheduled. In about 15 seconds the service restarts on {branch}"
                + (" (hard reset to origin)." if reset else " (local changes kept).") +
                f"\nCurrent commit: {head}\n"
                "This chat stays open. Ask me to check release_status to confirm the service is back.")

    def release_push(ctx, a):
        """Commit local changes and push them to GitHub using the stored token."""
        msg = (a.get("message") or "").strip()
        if not msg:
            raise ConnectorError("message is required, e.g. 'fix: chat list scroll'.")
        if not token:
            raise ConnectorError("No GitHub token is saved for this plugin. Add a personal access token "
                                 "(repo scope) in the Release connector settings, then try again.")
        redact = (token, f"x-access-token:{token}")
        _rc, remote = _sh("git remote get-url origin", 20)
        if remote.startswith("git@"):
            # Normalise SSH remotes to HTTPS so the token can be used.
            path = remote.split(":", 1)[-1]
            _sh(f"git remote set-url origin https://github.com/{shlex.quote(path)}", 20)
            remote = f"https://github.com/{path}"
        if not remote:
            raise ConnectorError("This repo has no 'origin' remote.")
        # Only tracked edits by default: the VM has big untracked build artifacts
        # (android/, node_modules/, mcp-deploy/) that must never be committed.
        paths = (a.get("paths") or "").strip()
        if paths:
            wanted = [p.strip() for p in re.split(r"[,\n]+", paths) if p.strip()]
            for p in wanted:
                _sh(f"git add -A -- {shlex.quote(p)}", 60)
        else:
            _sh("git add -u -- . ':(exclude)mcp-deploy'", 60)
        _rc, staged = _sh("git diff --cached --name-only | head -30", 20)
        commit = ""
        if staged:
            body = msg.replace('"', "'")
            rc, out = _sh(f'git -c user.name="ZerotheBot" -c user.email="bot@zerothebot.local" '
                          f'commit -m "{body}"', 60, redact=redact)
            if rc != 0:
                raise ConnectorError("Commit failed:\n" + out)
            _rc, commit = _sh("git rev-parse --short HEAD", 20)
        else:
            commit = "(nothing new to commit)"
        helper = "'!f() { echo username=x-access-token; echo \"password=$ZB_GH_TOKEN\"; }; f'"
        rc, out = _sh(f"git -c credential.helper={helper} push origin HEAD:main --verbose",
                      180, extra_env={"ZB_GH_TOKEN": token}, redact=redact)
        if rc != 0:
            raise ConnectorError("Push failed:\n" + out[-4000:])
        files = ("\nFiles: " + staged.replace("\n", ", ")) if staged else ""
        return (f"Pushed to GitHub: {commit}{files}\n\n" + out[-2000:] +
                "\n\nRun release_deploy to put this on the VM, or release_build_apk to ship a new APK.")

    return [
        T("release_status", "Show the app release state: current commit, uncommitted changes, whether an APK build is "
                            "running, the published APK version/download link, and whether the service answers.",
          {}, [], release_status, risk=lambda ctx, a: Risk("read", "Check app release status")),
        T("release_build_apk", "Build a new Android APK from the current code, publish it for download and return the "
                               "download link, version and sha256. Bumps the patch version and versionCode first so phones "
                               "see it as an update. The build runs detached and is polled; if it is still going you get the "
                               "log tail and can check again with release_status.",
          {"wait": {"type": "integer", "description": "Seconds to wait for the build before returning (default 90, max 540)."},
           "bump": {"type": "boolean", "description": "Bump rn-app/app.json version + versionCode first (default true)."}},
          [], release_build_apk,
          risk=lambda ctx, a: Risk("command", "Build and publish a new APK"),
          label=lambda a: "Build and publish the app APK"),
        T("release_deploy", "Put the code on the VM and restart the service. By default it hard-resets the VM repo to "
                            "origin/main first, then restarts detached (the restart happens a few seconds after this "
                            "reply is sent).",
          {"reset": {"type": "boolean", "description": "Reset to origin/main first (default true). Set false to deploy local changes."},
           "branch": {"type": "string", "description": "Branch to deploy (default main)."}},
          [], release_deploy,
          risk=lambda ctx, a: Risk("command", "Deploy the app and restart the service"),
          label=lambda a: "Deploy to the VM and restart"),
        T("release_push", "Commit and push to GitHub using the saved token. By default it commits only edits to files that "
                          "are already tracked (safe: VM build artifacts stay out). List extra paths to include new files. "
                          "mcp-deploy is always excluded.",
          {"message": {"type": "string", "description": "Commit message (required)."},
           "paths": {"type": "string", "description": "Optional comma-separated files/folders to add, including new ones. "
                                                     "Leave empty to commit only tracked-file edits."}},
          ["message"], release_push,
          risk=lambda ctx, a: Risk("command", f"Commit and push: {short(a.get('message', ''), 120)}",
                                  {"pattern": "git push"}),
          label=lambda a: f"Push to GitHub: {short(a.get('message', ''), 60)}"),
    ]


def build_android(c: Conn) -> list[ToolSpec]:
    """Control the user's own Android phone apps through the Device Bridge on their phone.
    The phone runs a poller (the ZerotheBot accessibility service in the app) that executes
    queued jobs and reports results. No cloud vendor needed."""
    from .db import jdump as _jdump

    def _job(ctx: ToolContext, cmd: str, args: dict, timeout: int = 45) -> str:
        eng = ctx.engine
        jid = str(int(time.time() * 1000)) + os.urandom(3).hex()
        eng.db.insert("device_jobs", {"id": jid, "device": "phone", "bot_id": ctx.bot["id"],
                                      "thread_id": ctx.thread_id, "turn_id": ctx.turn_id,
                                      "cmd": cmd, "args": _jdump(args), "created_at": time.time()})
        end = time.time() + timeout
        while time.time() < end:
            row = eng.db.one("SELECT status, result FROM device_jobs WHERE id=?", (jid,))
            if row and row["status"] in ("done", "error"):
                if row["status"] == "done":
                    return row["result"] or "Done."
                raise ConnectorError(row["result"] or "The phone could not do that.")
            row = eng.db.one("SELECT status FROM device_jobs WHERE id=?", (jid,))
            if row and row["status"] == "stale":
                raise ConnectorError("The phone did not pick this up. Open the ZerotheBot app on the phone and "
                                     "enable ZerotheBot in Accessibility (Settings > Accessibility > ZerotheBot).")
            time.sleep(1.0)
        eng.db.execute("UPDATE device_jobs SET status='stale', done_at=? WHERE id=? AND status != 'done'", (time.time(), jid))
        raise ConnectorError(f"The phone did not report back within {timeout}s. "
                             "Check the phone: the ZerotheBot app must be installed and the Device Bridge service enabled in Accessibility settings.")

    def android_open_app(ctx, a):
        app = (a.get("app") or "").strip()
        if not app:
            raise ConnectorError("app is required (app name like 'WhatsApp' or package name like 'com.whatsapp').")
        return _job(ctx, "open_app", {"app": app}, timeout=45)

    def android_tap(ctx, a):
        try:
            x = int(a.get("x"))
            y = int(a.get("y"))
        except (TypeError, ValueError):
            raise ConnectorError("x and y are required as integers (screen pixels).")
        return _job(ctx, "tap", {"x": x, "y": y}, timeout=45)

    def android_text(ctx, a):
        t = (a.get("text") or "").strip()
        if not t:
            raise ConnectorError("text is required.")
        return _job(ctx, "text", {"text": t}, timeout=45)

    def android_key(ctx, a):
        key = (a.get("key") or "").strip()
        if key not in ("back", "home", "recents", "enter", "backspace"):
            raise ConnectorError("key must be one of: back, home, recents, enter, backspace.")
        return _job(ctx, "key", {"key": key}, timeout=45)

    def android_ui(ctx, a):
        return _job(ctx, "ui", {}, timeout=45)

    def android_tap_text(ctx, a):
        t = (a.get("text") or "").strip()
        if not t:
            raise ConnectorError("text is required.")
        return _job(ctx, "tap_text", {"text": t}, timeout=45)

    def android_status(ctx, a):
        last = c.mgr.engine.db.one("SELECT done_at FROM device_jobs WHERE device='phone' AND status='done' ORDER BY done_at DESC LIMIT 1")
        return (f"Device bridge last talked to the server at {last['done_at']:.0f} (unix). "
                "If jobs are stale, ask the user to open the ZerotheBot phone app once so the bridge reconnects.") if last else \
            "No phone has reported to the device bridge yet. The user needs the ZerotheBot Android app with the Device Bridge enabled in Accessibility settings."

    return [
        T("android_open_app", "Open an app on the user's own Android phone. Pass the app's name "
                              "(for example 'WhatsApp', 'Instagram', 'Bank') or its package name ('com.whatsapp'). "
                              "The phone resolves the name against installed apps.",
          {"app": {"type": "string", "description": "App name or package id (required)."}},
          ["app"], android_open_app, risk=R("write", lambda a: f"Open {a.get('app')} on your phone"),
          label=lambda a: f"Open {a.get('app')} on your phone"),
        T("android_tap", "Tap a spot on the phone screen (screen pixel coordinates). Prefer android_tap_text or "
                         "android_ui first: those use the accessibility tree and are far more reliable.",
          {"x": {"type": "integer", "description": "X in screen pixels."},
           "y": {"type": "integer", "description": "Y in screen pixels."}},
          ["x", "y"], android_tap, risk=R("write", lambda a: f"Tap phone screen at {a.get('x')},{a.get('y')}"),
          label=lambda a: "Tap your phone screen"),
        T("android_ui", "Read the phone's current screen as a text list: every visible label, its class, whether it is "
                        "clickable/editable, and its tap point. Call this after android_open_app to see what is on screen "
                        "and decide what to tap or type next.",
          {}, [], android_ui, label=lambda a: "Read your phone screen"),
        T("android_tap_text", "Find an element on the phone screen by its visible text or accessibility label and tap it. "
                              "This is the safest way to press buttons in apps.",
          {"text": {"type": "string", "description": "Visible text or label to tap (required)."}},
          ["text"], android_tap_text, risk=R("write", lambda a: f"Tap '{a.get('text')}' on your phone"),
          label=lambda a: f"Tap '{a.get('text')}' on your phone"),
        T("android_text", "Type text into the focused field on the phone (uses clipboard + paste).",
          {"text": {"type": "string", "description": "Text to type (required)."}},
          ["text"], android_text, risk=R("write", lambda a: "Type text on your phone"),
          label=lambda a: "Type text on your phone"),
        T("android_key", "Press a system key on the phone: back, home, recents, enter or backspace.",
          {"key": {"type": "string", "description": "back | home | recents | enter | backspace (required)."}},
          ["key"], android_key, risk=R("write", lambda a: f"Press {a.get('key')} on your phone"),
          label=lambda a: f"Press {a.get('key')} on your phone"),
        T("android_status", "Check whether the phone's Device Bridge is connected and when it last reported.",
          {}, [], android_status, label=lambda a: "Check device bridge status"),
    ]


def build_calendar(c: Conn) -> list[ToolSpec]:
    base = "https://www.googleapis.com/calendar/v3"

    def H() -> dict:
        return c.google_headers()

    def events(ctx, a):
        res = c.http(ctx, "GET", f"{base}/calendars/{quote(a.get('calendar_id', 'primary'))}/events", headers=H(),
                     params={"timeMin": a["time_min"], "timeMax": a["time_max"], "singleEvents": "true", "orderBy": "startTime", "maxResults": 50})
        return [{"id": e["id"], "summary": e.get("summary"), "start": e.get("start"), "end": e.get("end"), "location": e.get("location"),
                 "attendees": [x.get("email") for x in e.get("attendees", [])]} for e in res.get("items", [])] or "No events."

    def free(ctx, a):
        res = c.http(ctx, "POST", f"{base}/freeBusy", headers=H(), json_body={"timeMin": a["time_min"], "timeMax": a["time_max"],
                                                                              "items": [{"id": x} for x in a.get("calendars", ["primary"])]})
        return res.get("calendars", {})

    def create(ctx, a):
        body = {"summary": a["summary"], "description": a.get("description", ""), "location": a.get("location", ""),
                "start": {"dateTime": a["start"]}, "end": {"dateTime": a["end"]}}
        if a.get("attendees"):
            body["attendees"] = [{"email": x} for x in a["attendees"]]
        e = c.http(ctx, "POST", f"{base}/calendars/primary/events", headers=H(), params={"sendUpdates": "all" if a.get("attendees") else "none"}, json_body=body)
        return f"Event created: {e.get('htmlLink')}"

    def delete(ctx, a):
        c.http(ctx, "DELETE", f"{base}/calendars/primary/events/{a['event_id']}", headers=H())
        return "Event deleted."

    def create_risk(ctx: ToolContext, a: dict) -> Risk | None:
        if a.get("attendees"):
            return Risk("send", f"Create event '{a.get('summary')}' and invite {', '.join(a['attendees'])}", {"pattern": "invite", "start": a.get("start")})
        return None

    return [
        T("gcal_list_events", "List calendar events in a time range (RFC3339 times with timezone).",
          {"time_min": s("RFC3339 start"), "time_max": s("RFC3339 end"), "calendar_id": s("Calendar id, default primary")}, ["time_min", "time_max"],
          events, read=True, untrusted="Google Calendar", label=lambda a: "List calendar events"),
        T("gcal_freebusy", "Find busy periods to propose meeting times.", {"time_min": s("RFC3339"), "time_max": s("RFC3339"),
                                                                            "calendars": {"type": "array", "items": {"type": "string"}}},
          ["time_min", "time_max"], free, read=True, untrusted="Google Calendar", label=lambda a: "Check free/busy"),
        T("gcal_create_event", "Create an event. Inviting attendees sends invitations and needs approval.",
          {"summary": s("Title"), "start": s("RFC3339 start"), "end": s("RFC3339 end"), "description": s("Description"), "location": s("Location"),
           "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee emails"}}, ["summary", "start", "end"], create,
          risk=create_risk, label=lambda a: f"Create event: {a.get('summary')}"),
        T("gcal_delete_event", "Delete an event by id. Requires approval.", {"event_id": s("Event id")}, ["event_id"], delete,
          risk=R("delete", lambda a: f"Delete calendar event {a.get('event_id')}", lambda a: "event"), label=lambda a: "Delete calendar event"),
    ]


def build_slack(c: Conn) -> list[ToolSpec]:
    api = "https://slack.com/api/"

    def call(ctx, method: str, params: dict | None = None, user: bool = False, post: bool = False) -> dict:
        tok = c.cfg("user_token") if user else (c.cfg("bot_token") or c.cfg("user_token"))
        if not tok:
            raise ConnectorError("Slack token missing. Add it in Plugins.")
        h = {"Authorization": f"Bearer {tok}"}
        res = c.http(ctx, "POST" if post else "GET", api + method, headers=h, params=None if post else params,
                     json_body=params if post else None)
        if isinstance(res, dict) and not res.get("ok"):
            raise ConnectorError(f"Slack error: {res.get('error')}")
        return res

    def channel_id(ctx, name: str) -> str:
        if re.match(r"^[CGD][A-Z0-9]{6,}$", name):
            return name
        cursor = ""
        for _ in range(10):
            r = call(ctx, "conversations.list", {"limit": 200, "types": "public_channel,private_channel", "cursor": cursor})
            for ch in r.get("channels", []):
                if ch["name"] == name.lstrip("#"):
                    return ch["id"]
            cursor = r.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        raise ConnectorError(f"Channel {name} not found (or the app is not in it).")

    def list_ch(ctx, a):
        r = call(ctx, "conversations.list", {"limit": 200, "types": "public_channel,private_channel", "exclude_archived": "true"})
        return [{"id": x["id"], "name": x["name"], "members": x.get("num_members"), "topic": x.get("topic", {}).get("value", "")} for x in r.get("channels", [])]

    def read(ctx, a):
        r = call(ctx, "conversations.history", {"channel": channel_id(ctx, a["channel"]), "limit": min(int(a.get("limit", 20)), 100)})
        return [{"ts": m.get("ts"), "user": m.get("user") or m.get("bot_id"), "text": m.get("text"), "thread_ts": m.get("thread_ts"),
                 "replies": m.get("reply_count")} for m in r.get("messages", [])]

    def post(ctx, a):
        body = {"channel": channel_id(ctx, a["channel"]), "text": a["text"]}
        if a.get("thread_ts"):
            body["thread_ts"] = a["thread_ts"]
        r = call(ctx, "chat.postMessage", body, post=True)
        return f"Posted to {a['channel']} (ts {r.get('ts')})."

    def search(ctx, a):
        r = call(ctx, "search.messages", {"query": a["query"], "count": 10}, user=True)
        return [{"channel": m.get("channel", {}).get("name"), "user": m.get("username"), "text": m.get("text"), "ts": m.get("ts"),
                 "permalink": m.get("permalink")} for m in r.get("messages", {}).get("matches", [])]

    return [
        T("slack_list_channels", "List Slack channels.", {}, [], list_ch, read=True, untrusted="Slack", label=lambda a: "List Slack channels"),
        T("slack_read_channel", "Read recent messages from a channel (name or id).", {"channel": s("Channel name or id"), "limit": i("Number of messages")},
          ["channel"], read, read=True, untrusted="Slack messages", label=lambda a: f"Read Slack {a.get('channel')}"),
        T("slack_post_message", "Post a message to a channel. Requires approval.", {"channel": s("Channel name or id"), "text": s("Message text"),
                                                                                    "thread_ts": s("Reply in thread")}, ["channel", "text"], post,
          risk=R("send", lambda a: f"Post to Slack {a.get('channel')}: {short(a.get('text', ''), 160)}", lambda a: str(a.get("channel")),
                 detail=lambda a: {"channel": a.get("channel"), "text": a.get("text")}), label=lambda a: f"Post to Slack {a.get('channel')}"),
        T("slack_search", "Search Slack messages (needs a user token).", {"query": s("Search query")}, ["query"], search, read=True,
          untrusted="Slack search", label=lambda a: f"Search Slack: {a.get('query')}"),
    ]


def build_notion(c: Conn) -> list[ToolSpec]:
    base = "https://api.notion.com/v1"

    def H() -> dict:
        return {"Authorization": f"Bearer {c.cfg('token')}", "Notion-Version": "2022-06-28"}

    def plain(rt: list) -> str:
        return "".join(x.get("plain_text", "") for x in rt or [])

    def search(ctx, a):
        r = c.http(ctx, "POST", f"{base}/search", headers=H(), json_body={"query": a["query"], "page_size": 10})
        out = []
        for x in r.get("results", []):
            title = ""
            if x["object"] == "page":
                for p in x.get("properties", {}).values():
                    if p.get("type") == "title":
                        title = plain(p["title"])
            else:
                title = plain(x.get("title", []))
            out.append({"id": x["id"], "type": x["object"], "title": title, "url": x.get("url")})
        return out

    def read(ctx, a):
        r = c.http(ctx, "GET", f"{base}/blocks/{a['page_id']}/children", headers=H(), params={"page_size": 100})
        lines = []
        for blk in r.get("results", []):
            t = blk["type"]
            lines.append(plain(blk.get(t, {}).get("rich_text", [])) if isinstance(blk.get(t), dict) else "")
        return "\n".join(l for l in lines if l)

    def paras(text: str) -> list[dict]:
        return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": p[:1900]}}]}}
                for p in text.split("\n\n") if p.strip()][:90]

    def create(ctx, a):
        body = {"parent": {"page_id": a["parent_page_id"]}, "properties": {"title": {"title": [{"text": {"content": a["title"]}}]}},
                "children": paras(a.get("content", ""))}
        r = c.http(ctx, "POST", f"{base}/pages", headers=H(), json_body=body)
        return f"Page created: {r.get('url')}"

    def append(ctx, a):
        c.http(ctx, "PATCH", f"{base}/blocks/{a['page_id']}/children", headers=H(), json_body={"children": paras(a["text"])})
        return "Appended."

    return [
        T("notion_search", "Search Notion pages and databases.", {"query": s("Query")}, ["query"], search, read=True, untrusted="Notion", label=lambda a: f"Search Notion: {a.get('query')}"),
        T("notion_read_page", "Read a Notion page's text content.", {"page_id": s("Page id")}, ["page_id"], read, read=True, untrusted="a Notion page",
          label=lambda a: "Read Notion page"),
        T("notion_create_page", "Create a page under a parent page. Requires approval.", {"parent_page_id": s("Parent page id"), "title": s("Title"),
                                                                                         "content": s("Body text; blank line separates paragraphs")},
          ["parent_page_id", "title"], create, risk=R("write", lambda a: f"Create Notion page '{a.get('title')}'", lambda a: "notion-create"),
          label=lambda a: f"Create Notion page {a.get('title')}"),
        T("notion_append", "Append text to a page. Requires approval.", {"page_id": s("Page id"), "text": s("Text")}, ["page_id", "text"], append,
          risk=R("write", lambda a: f"Append to Notion page {a.get('page_id')}: {short(a.get('text', ''), 120)}", lambda a: "notion-append"),
          label=lambda a: "Append to Notion page"),
    ]


def build_linear(c: Conn) -> list[ToolSpec]:
    def gql(ctx, query: str, variables: dict | None = None) -> dict:
        r = c.http(ctx, "POST", "https://api.linear.app/graphql", headers={"Authorization": c.cfg("api_key"), "Content-Type": "application/json"},
                   json_body={"query": query, "variables": variables or {}})
        if isinstance(r, dict) and r.get("errors"):
            raise ConnectorError("Linear error: " + short(r["errors"][0].get("message", ""), 200))
        return r["data"]

    ISSUE = "id identifier title description priority url state{name} assignee{name} team{key} comments(first:10){nodes{body user{name} createdAt}}"

    def search(ctx, a):
        d = gql(ctx, "query($q:String!){issues(first:15, filter:{or:[{title:{containsIgnoreCase:$q}},{description:{containsIgnoreCase:$q}}]}){nodes{identifier title url state{name} assignee{name} priority}}}", {"q": a["query"]})
        return d["issues"]["nodes"] or "No issues found."

    def get(ctx, a):
        return gql(ctx, "query($id:String!){issue(id:$id){" + ISSUE + "}}", {"id": a["issue"]})["issue"]

    def create(ctx, a):
        team = gql(ctx, "query($k:String!){teams(filter:{key:{eq:$k}}){nodes{id}}}", {"k": a["team_key"]})["teams"]["nodes"]
        if not team:
            raise ConnectorError(f"No Linear team with key {a['team_key']}.")
        inp = {"teamId": team[0]["id"], "title": a["title"], "description": a.get("description", "")}
        if a.get("priority") is not None:
            inp["priority"] = int(a["priority"])
        d = gql(ctx, "mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{identifier url}}}", {"i": inp})
        return f"Created {d['issueCreate']['issue']['identifier']}: {d['issueCreate']['issue']['url']}"

    def comment(ctx, a):
        iid = gql(ctx, "query($id:String!){issue(id:$id){id}}", {"id": a["issue"]})["issue"]["id"]
        gql(ctx, "mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}", {"i": {"issueId": iid, "body": a["body"]}})
        return "Comment added."

    return [
        T("linear_search_issues", "Search Linear issues by text.", {"query": s("Text")}, ["query"], search, read=True, untrusted="Linear", label=lambda a: f"Search Linear: {a.get('query')}"),
        T("linear_get_issue", "Get a Linear issue by identifier (e.g. ENG-123).", {"issue": s("Identifier or id")}, ["issue"], get, read=True, untrusted="a Linear issue",
          label=lambda a: f"Read Linear {a.get('issue')}"),
        T("linear_create_issue", "Create a Linear issue. Requires approval.", {"team_key": s("Team key, e.g. ENG"), "title": s("Title"), "description": s("Markdown description"),
                                                                              "priority": i("0 none, 1 urgent .. 4 low")}, ["team_key", "title"], create,
          risk=R("write", lambda a: f"Create Linear issue in {a.get('team_key')}: {a.get('title')}", lambda a: "linear-create"), label=lambda a: f"Create Linear issue"),
        T("linear_comment", "Comment on a Linear issue. Requires approval.", {"issue": s("Identifier"), "body": s("Comment markdown")}, ["issue", "body"], comment,
          risk=R("write", lambda a: f"Comment on Linear {a.get('issue')}: {short(a.get('body', ''), 140)}", lambda a: "linear-comment"), label=lambda a: "Comment on Linear issue"),
    ]


def build_jira(c: Conn) -> list[ToolSpec]:
    def base() -> str:
        return c.cfg("base_url").rstrip("/")

    def H() -> dict:
        tok = base64.b64encode(f"{c.cfg('email')}:{c.cfg('api_token')}".encode()).decode()
        return {"Authorization": f"Basic {tok}", "Accept": "application/json", "Content-Type": "application/json"}

    def adf(text: str) -> dict:
        return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in text.split("\n\n") if p.strip()] or
                [{"type": "paragraph", "content": []}]}

    def flat(adf_node: Any) -> str:
        if isinstance(adf_node, dict):
            if adf_node.get("type") == "text":
                return adf_node.get("text", "")
            return " ".join(flat(x) for x in adf_node.get("content", []))
        return ""

    def search(ctx, a):
        r = c.http(ctx, "GET", f"{base()}/rest/api/3/search/jql", headers=H(),
                   params={"jql": a["jql"], "maxResults": min(int(a.get("max_results", 15)), 50), "fields": "summary,status,assignee,priority,issuetype"})
        return [{"key": x["key"], "summary": x["fields"].get("summary"), "status": (x["fields"].get("status") or {}).get("name"),
                 "assignee": (x["fields"].get("assignee") or {}).get("displayName"), "priority": (x["fields"].get("priority") or {}).get("name")}
                for x in r.get("issues", [])] or "No issues."

    def get(ctx, a):
        x = c.http(ctx, "GET", f"{base()}/rest/api/3/issue/{a['key']}", headers=H(), params={"fields": "summary,description,status,assignee,comment"})
        f = x["fields"]
        return {"key": x["key"], "summary": f.get("summary"), "status": (f.get("status") or {}).get("name"), "description": flat(f.get("description"))[:6000],
                "comments": [{"author": (cm.get("author") or {}).get("displayName"), "body": flat(cm.get("body"))[:1000]} for cm in (f.get("comment") or {}).get("comments", [])[-10:]]}

    def create(ctx, a):
        r = c.http(ctx, "POST", f"{base()}/rest/api/3/issue", headers=H(),
                   json_body={"fields": {"project": {"key": a["project_key"]}, "summary": a["summary"], "issuetype": {"name": a.get("issue_type", "Task")},
                                         "description": adf(a.get("description", ""))}})
        return f"Created {r['key']}: {base()}/browse/{r['key']}"

    def comment(ctx, a):
        c.http(ctx, "POST", f"{base()}/rest/api/3/issue/{a['key']}/comment", headers=H(), json_body={"body": adf(a["body"])})
        return "Comment added."

    return [
        T("jira_search", "Search Jira issues with JQL.", {"jql": s("JQL query"), "max_results": i("Max results")}, ["jql"], search, read=True, untrusted="Jira", label=lambda a: f"Search Jira: {a.get('jql')}"),
        T("jira_get_issue", "Get a Jira issue by key.", {"key": s("Issue key, e.g. OPS-12")}, ["key"], get, read=True, untrusted="a Jira issue", label=lambda a: f"Read Jira {a.get('key')}"),
        T("jira_create_issue", "Create a Jira issue. Requires approval.", {"project_key": s("Project key"), "summary": s("Summary"), "description": s("Description"),
                                                                          "issue_type": s("Task, Bug, Story...")}, ["project_key", "summary"], create,
          risk=R("write", lambda a: f"Create Jira issue in {a.get('project_key')}: {a.get('summary')}", lambda a: "jira-create"), label=lambda a: "Create Jira issue"),
        T("jira_comment", "Comment on a Jira issue. Requires approval.", {"key": s("Issue key"), "body": s("Comment")}, ["key", "body"], comment,
          risk=R("write", lambda a: f"Comment on Jira {a.get('key')}: {short(a.get('body', ''), 140)}", lambda a: "jira-comment"), label=lambda a: "Comment on Jira issue"),
    ]


def build_github(c: Conn) -> list[ToolSpec]:
    def base() -> str:
        return (c.cfg("api_url") or "https://api.github.com").rstrip("/")

    def H() -> dict:
        return {"Authorization": f"Bearer {c.cfg('token')}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    def repos(ctx, a):
        r = c.http(ctx, "GET", f"{base()}/user/repos", headers=H(), params={"per_page": 50, "sort": "updated"})
        return [{"full_name": x["full_name"], "private": x["private"], "description": x.get("description"), "open_issues": x.get("open_issues_count")} for x in r]

    def issues(ctx, a):
        r = c.http(ctx, "GET", f"{base()}/repos/{a['repo']}/issues", headers=H(), params={"state": a.get("state", "open"), "per_page": 30, "labels": a.get("labels", "")})
        return [{"number": x["number"], "title": x["title"], "state": x["state"], "labels": [l["name"] for l in x["labels"]], "user": x["user"]["login"],
                 "is_pr": "pull_request" in x, "updated": x["updated_at"]} for x in r] or "No issues."

    def get_issue(ctx, a):
        x = c.http(ctx, "GET", f"{base()}/repos/{a['repo']}/issues/{a['number']}", headers=H())
        cm = c.http(ctx, "GET", f"{base()}/repos/{a['repo']}/issues/{a['number']}/comments", headers=H(), params={"per_page": 20})
        return {"number": x["number"], "title": x["title"], "state": x["state"], "body": (x.get("body") or "")[:6000], "labels": [l["name"] for l in x["labels"]],
                "comments": [{"user": y["user"]["login"], "body": y["body"][:1200]} for y in cm]}

    def prs(ctx, a):
        r = c.http(ctx, "GET", f"{base()}/repos/{a['repo']}/pulls", headers=H(), params={"state": a.get("state", "open"), "per_page": 30})
        return [{"number": x["number"], "title": x["title"], "user": x["user"]["login"], "head": x["head"]["ref"], "base": x["base"]["ref"], "draft": x.get("draft")} for x in r] or "No pull requests."

    def get_file(ctx, a):
        r = c.http(ctx, "GET", f"{base()}/repos/{a['repo']}/contents/{a['path'].lstrip('/')}", headers=H(), params={"ref": a.get("ref", "")} if a.get("ref") else None)
        if isinstance(r, list):
            return [{"name": x["name"], "type": x["type"]} for x in r]
        return base64.b64decode(r.get("content", "")).decode("utf-8", "replace")[:20000]

    def search_code(ctx, a):
        r = c.http(ctx, "GET", f"{base()}/search/code", headers=H(), params={"q": a["query"], "per_page": 10})
        return [{"repo": x["repository"]["full_name"], "path": x["path"], "url": x["html_url"]} for x in r.get("items", [])]

    def create_issue(ctx, a):
        r = c.http(ctx, "POST", f"{base()}/repos/{a['repo']}/issues", headers=H(), json_body={"title": a["title"], "body": a.get("body", ""), "labels": a.get("labels", [])})
        return f"Created issue #{r['number']}: {r['html_url']}"

    def comment(ctx, a):
        r = c.http(ctx, "POST", f"{base()}/repos/{a['repo']}/issues/{a['number']}/comments", headers=H(), json_body={"body": a["body"]})
        return f"Comment posted: {r['html_url']}"

    def create_pr(ctx, a):
        r = c.http(ctx, "POST", f"{base()}/repos/{a['repo']}/pulls", headers=H(), json_body={"title": a["title"], "head": a["head"], "base": a["base"], "body": a.get("body", "")})
        return f"Pull request #{r['number']} opened: {r['html_url']}"

    rp = {"repo": s("owner/name")}
    return [
        T("github_list_repos", "List repositories you can access.", {}, [], repos, read=True, untrusted="GitHub", label=lambda a: "List GitHub repos"),
        T("github_list_issues", "List issues in a repo.", {**rp, "state": s("open|closed|all"), "labels": s("Comma-separated labels")}, ["repo"], issues, read=True,
          untrusted="GitHub issues", label=lambda a: f"List issues in {a.get('repo')}"),
        T("github_get_issue", "Read an issue or PR conversation.", {**rp, "number": i("Issue number")}, ["repo", "number"], get_issue, read=True, untrusted="a GitHub issue",
          label=lambda a: f"Read {a.get('repo')}#{a.get('number')}"),
        T("github_list_prs", "List pull requests.", {**rp, "state": s("open|closed|all")}, ["repo"], prs, read=True, untrusted="GitHub PRs", label=lambda a: f"List PRs in {a.get('repo')}"),
        T("github_get_file", "Read a file or list a folder from a repo.", {**rp, "path": s("Path"), "ref": s("Branch/tag/sha")}, ["repo", "path"], get_file, read=True,
          untrusted="a repository file", label=lambda a: f"Read {a.get('repo')}:{a.get('path')}"),
        T("github_search_code", "Search code across GitHub.", {"query": s("GitHub code search query")}, ["query"], search_code, read=True, untrusted="GitHub code search",
          label=lambda a: f"Search code: {a.get('query')}"),
        T("github_create_issue", "Open an issue. Requires approval.", {**rp, "title": s("Title"), "body": s("Body"), "labels": {"type": "array", "items": {"type": "string"}}},
          ["repo", "title"], create_issue, risk=R("write", lambda a: f"Open GitHub issue in {a.get('repo')}: {a.get('title')}", lambda a: str(a.get("repo"))),
          label=lambda a: "Open GitHub issue"),
        T("github_comment", "Comment on an issue/PR. Requires approval.", {**rp, "number": i("Number"), "body": s("Comment")}, ["repo", "number", "body"], comment,
          risk=R("write", lambda a: f"Comment on {a.get('repo')}#{a.get('number')}: {short(a.get('body', ''), 140)}", lambda a: str(a.get("repo"))), label=lambda a: "Comment on GitHub"),
        T("github_create_pr", "Open a pull request from an already-pushed branch. Requires approval.",
          {**rp, "title": s("Title"), "head": s("Head branch"), "base": s("Base branch"), "body": s("Description")}, ["repo", "title", "head", "base"], create_pr,
          risk=R("write", lambda a: f"Open PR in {a.get('repo')}: {a.get('title')} ({a.get('head')} -> {a.get('base')})", lambda a: str(a.get("repo"))), label=lambda a: "Open pull request"),
    ]


def check_http(url_fn: Callable[[Conn], tuple[str, dict]]) -> Callable[[Conn], str]:
    def check(c: Conn) -> str:
        import httpx
        url, headers = url_fn(c)
        r = httpx.get(url, headers=headers, timeout=20)
        if r.status_code >= 400:
            raise ConnectorError(f"HTTP {r.status_code}: {short(r.text, 160)}")
        return "Connection OK."
    return check


def _google_check(path: str) -> Callable[[Conn], str]:
    def check(c: Conn) -> str:
        import httpx
        r = httpx.get(path, headers=c.google_headers(), timeout=20)
        if r.status_code >= 400:
            raise ConnectorError(f"HTTP {r.status_code}: {short(r.text, 160)}")
        return "Signed in and reachable."
    return check


GOOGLE_FIELDS = [
    {"key": "client_id", "label": "Google OAuth client ID", "secret": False, "required": True, "placeholder": "xxxx.apps.googleusercontent.com"},
    {"key": "client_secret", "label": "Google OAuth client secret", "secret": True, "required": True},
]


def builtin_defs() -> list[PluginDef]:
    return [
        PluginDef("gmail", "Gmail", "Read, search, draft and (with approval) send email.", fields=GOOGLE_FIELDS,
                  oauth={"provider": "google", "scopes": ["https://mail.google.com/"]}, build=build_gmail,
                  check=_google_check("https://gmail.googleapis.com/gmail/v1/users/me/profile"),
                  help="Create an OAuth client (type: Desktop app) in Google Cloud Console, enable the Gmail API, paste the client id/secret, then press Connect.",
                  setup_url="https://console.cloud.google.com/apis/credentials", setup_label="Open Google Cloud Console"),
        PluginDef("google_calendar", "Google Calendar", "Read your calendar, find free time, create events.", fields=GOOGLE_FIELDS,
                  oauth={"provider": "google", "scopes": ["https://www.googleapis.com/auth/calendar"]}, build=build_calendar,
                  check=_google_check("https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1"),
                  help="Uses the same Google OAuth client as Gmail if you leave the fields blank. Enable the Calendar API."),
        PluginDef("gdocs", "Google Docs, Sheets & Slides", "Create, read and update Docs, Sheets and Slides; files you create can be attached to email.",
                  fields=GOOGLE_FIELDS,
                  oauth={"provider": "google", "scopes": ["https://www.googleapis.com/auth/documents",
                                                           "https://www.googleapis.com/auth/spreadsheets",
                                                           "https://www.googleapis.com/auth/presentations",
                                                           "https://www.googleapis.com/auth/drive.file"]},
                  build=build_gdocs, check=_google_check("https://www.googleapis.com/drive/v3/files?pageSize=1&fields=files(id)"),
                  help="Uses the same Google OAuth client as Gmail if you leave the fields blank. Enable the Google Docs, Sheets, Slides and Drive APIs."),
        PluginDef("gdrive", "Google Drive", "Search, read, upload, share and organize files in the user's Google Drive.",
                  fields=GOOGLE_FIELDS,
                  oauth={"provider": "google", "scopes": ["https://www.googleapis.com/auth/drive"]},
                  build=build_gdrive, check=_google_check("https://www.googleapis.com/drive/v3/about?fields=user"),
                  help="Uses the same Google OAuth client as Gmail if you leave the fields blank. Enable the Google Drive API."),
        PluginDef("documents", "Documents (PDF & Word)", "Read text from PDF and Word files, and create new PDF/DOCX files in the workspace.",
                  build=build_documents, help="Works out of the box — no setup needed. Files are saved in the Bot workspace and show up in the Files screen."),
        PluginDef("shell", "Host Shell (admin)", "Run commands on the server that runs ZerotheBot — the machine with the app code. For engineer Bots that fix, build and deploy things.",
                  build=build_shell, help="Works out of the box — no setup needed. Commands run with the service user on the host VM. "
                                          "Every command shows an approval card; press 'Always' once to trust that command type for the Bot."),
        PluginDef("android", "My Android phone", "Open apps on the user's own phone, tap, type and press keys through the Device Bridge.",
                  build=build_android, help="Install the ZerotheBot app on the phone, then enable 'ZerotheBot' in Settings > Accessibility. "
                                            "The app's Device Bridge then executes tasks the Bot queues for the phone."),
        PluginDef("release", "App Release (build & deploy)", "Build the Android APK, publish it for download, deploy the code to the VM and push to GitHub.",
                  fields=[{"key": "github_token", "label": "GitHub token (repo scope, for push)", "secret": True, "required": False},
                          {"key": "repo_path", "label": "Repo path on the host", "secret": False, "required": False,
                           "placeholder": "/data/home/zerothebot"},
                          {"key": "apk_base", "label": "APK host URL", "secret": False, "required": False,
                           "placeholder": "https://8888-....instabox.tech"}],
                  build=build_release,
                  help="Works out of the box for building, publishing and deploying on the host VM (node, Java and the Android SDK are "
                       "already installed). Add a GitHub personal access token with 'repo' scope if you also want push to work."),
        PluginDef("slack", "Slack", "Read channels and post messages (with approval).",
                  fields=[{"key": "bot_token", "label": "Bot token (xoxb-...)", "secret": True, "required": True},
                           {"key": "user_token", "label": "User token (xoxp-..., optional, for search)", "secret": True, "required": False}],
                  build=build_slack, check=check_http(lambda c: ("https://slack.com/api/auth.test", {"Authorization": f"Bearer {c.cfg('bot_token') or c.cfg('user_token')}"})),
                  help="Create a Slack app, add scopes channels:read, channels:history, groups:read, chat:write, and install it to your workspace.",
                  setup_url="https://api.slack.com/apps", setup_label="Create a Slack app"),
        PluginDef("notion", "Notion", "Search, read and write Notion pages.", fields=[{"key": "token", "label": "Internal integration token", "secret": True, "required": True}],
                  build=build_notion, check=check_http(lambda c: ("https://api.notion.com/v1/users/me", {"Authorization": f"Bearer {c.cfg('token')}", "Notion-Version": "2022-06-28"})),
                  help="Create an internal integration at notion.so/my-integrations and share your pages with it.",
                  setup_url="https://www.notion.so/my-integrations", setup_label="Create a Notion integration"),
        PluginDef("linear", "Linear", "Search, read, create and comment on issues.", fields=[{"key": "api_key", "label": "Personal API key", "secret": True, "required": True}],
                  build=build_linear, help="Create a personal API key in Linear > Settings > API.",
                  setup_url="https://linear.app/settings/api", setup_label="Create a Linear API key"),
        PluginDef("jira", "Jira", "Search, read, create and comment on Jira Cloud issues.",
                  fields=[{"key": "base_url", "label": "Site URL", "secret": False, "required": True, "placeholder": "https://yourteam.atlassian.net"},
                           {"key": "email", "label": "Account email", "secret": False, "required": True},
                           {"key": "api_token", "label": "API token", "secret": True, "required": True}], build=build_jira,
                  check=check_http(lambda c: (c.cfg("base_url").rstrip("/") + "/rest/api/3/myself",
                                               {"Authorization": "Basic " + base64.b64encode(f"{c.cfg('email')}:{c.cfg('api_token')}".encode()).decode()})),
                  help="Create an API token at id.atlassian.com > Security > API tokens.",
                  setup_url="https://id.atlassian.com/manage-profile/security/api-tokens", setup_label="Create a Jira API token"),
        PluginDef("github", "GitHub", "Issues, pull requests, files and code search.",
                  fields=[{"key": "token", "label": "Personal access token", "secret": True, "required": True},
                           {"key": "api_url", "label": "API URL (GitHub Enterprise only)", "secret": False, "required": False, "placeholder": "https://api.github.com"}],
                  build=build_github, check=check_http(lambda c: ((c.cfg("api_url") or "https://api.github.com").rstrip("/") + "/user", {"Authorization": f"Bearer {c.cfg('token')}"})),
                  help="Create a fine-grained token with Issues, Pull requests and Contents access to the repos you want.",
                  setup_url="https://github.com/settings/tokens", setup_label="Create a GitHub token"),
    ]


# ----------------------------------------------------------------------------- generic REST + declarative plugins
_TOKEN_RE = re.compile(r"\{\{\s*(secret|config)\.(\w+)\s*\}\}|\{(\w+)\}")


def _subst(template: Any, args: dict, conn: Conn, in_url: bool = False) -> Any:
    """Fill {param} from the model's arguments and {{secret.x}}/{{config.x}} from stored config.

    One regex pass, so text supplied by the model can never expand into a secret reference.
    """
    if isinstance(template, dict):
        return {k: _subst(v, args, conn, in_url) for k, v in template.items()}
    if isinstance(template, list):
        return [_subst(v, args, conn, in_url) for v in template]
    if not isinstance(template, str):
        return template

    def repl(m: re.Match) -> str:
        if m.group(1):
            return conn.cfg(m.group(2))
        key = m.group(3)
        if key not in args:
            return m.group(0)
        v = str(args[key])
        return quote(v, safe="") if in_url else v
    return _TOKEN_RE.sub(repl, template)


def build_declarative(manifest: dict) -> Callable[[Conn], list[ToolSpec]]:
    def build(c: Conn) -> list[ToolSpec]:
        specs = []
        base_url = manifest.get("base_url", "")
        for t in manifest.get("tools", []):
            req = t.get("request", {})
            risk_kind = t.get("risk", "safe")

            def handler(ctx: ToolContext, args: dict, _req=req, _t=t) -> ToolResult:
                url = _subst(_req.get("url") or (base_url.rstrip("/") + "/" + _req.get("path", "").lstrip("/")), args, c, in_url=True)
                method = _req.get("method", "GET").upper()
                data = c.http(ctx, method, url, headers=_subst(_req.get("headers", {}), args, c), params=_subst(_req.get("query", {}), args, c) or None,
                              json_body=_subst(_req.get("body"), args, c))
                path = _t.get("response_path")
                if path and isinstance(data, (dict, list)):
                    for part in path.split("."):
                        data = data[int(part)] if isinstance(data, list) else data.get(part, data)
                return _res(data, manifest.get("name", manifest["id"]))

            risk = None
            if risk_kind != "safe":
                risk = R(risk_kind if risk_kind in ("send", "write", "delete", "submit", "purchase") else "write",
                         lambda a, _n=t["name"], _p=manifest["name"]: f"{_p}: {_n} {short(json.dumps(a), 200)}", lambda a, _n=t["name"]: _n)
            specs.append(ToolSpec(safe_name(f"{manifest['id']}_{t['name']}"), t.get("description", t["name"]), t.get("input_schema") or obj(), handler, risk=risk,
                                  label=lambda a, _n=t["name"], _p=manifest["name"]: f"{_p}: {_n}", read_only=risk_kind == "safe"))
        return specs
    return build


class PluginAPI:
    """Passed to python plugins' register(api)."""

    def __init__(self, conn: Conn):
        self._c = conn
        self.config = lambda key, default="": conn.cfg(key, default)

    def http(self, ctx: ToolContext, method: str, url: str, **kw: Any) -> Any:
        return self._c.http(ctx, method, url, **kw)


def build_python(path: Path, manifest: dict) -> Callable[[Conn], list[ToolSpec]]:
    def build(c: Conn) -> list[ToolSpec]:
        spec = importlib.util.spec_from_file_location(f"gbplugin_{manifest['id']}", path / manifest.get("entry", "plugin.py"))
        if not spec or not spec.loader:
            raise ConnectorError("Cannot load plugin entry point.")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tools = mod.register(PluginAPI(c))
        out = []
        for t in tools:
            fn = t["handler"]
            risk = None
            if t.get("risk") and t["risk"] != "safe":
                risk = R(t["risk"] if t["risk"] in ("send", "write", "delete", "submit", "purchase", "command") else "write",
                         lambda a, _n=t["name"]: f"{manifest['name']}: {_n} {short(json.dumps(a, default=str), 200)}", lambda a, _n=t["name"]: _n)

            def handler(ctx: ToolContext, args: dict, _fn=fn) -> ToolResult:
                if sandbox.should_isolate(manifest):
                    fields = manifest.get("fields") or []
                    config = {f["key"]: c.cfg(f["key"], f.get("default", "")) for f in fields
                              if isinstance(f, dict) and f.get("key")}
                    layers = c.mgr.engine.net.export(ctx.bot["id"] if ctx and ctx.bot else None)
                    d = sandbox.run_tool(plugin_dir=str(path), tool=t["name"], args=args,
                                         bot=(ctx.bot if ctx else {}), thread_id=ctx.thread_id if ctx else "",
                                         turn_id=ctx.turn_id if ctx else "", config=config, net_layers=layers,
                                         workspace=str(c.mgr.engine.computer.workspace),
                                         timeout=int(manifest.get("timeout", 180) or 180))
                    return ToolResult(text=d.get("text", ""), data=d.get("data", ""), images=d.get("images") or [],
                                      is_error=d.get("is_error", False), url=d.get("url", ""),
                                      path=d.get("path", ""), untrusted=d.get("untrusted", ""))
                out_ = _fn(ctx, args) if len(inspect.signature(_fn).parameters) >= 2 else _fn(args)
                return out_ if isinstance(out_, ToolResult) else _res(out_, manifest["name"])
            out.append(ToolSpec(safe_name(f"{manifest['id']}_{t['name']}"), t.get("description", t["name"]), t.get("input_schema") or obj(), handler, risk=risk,
                                label=lambda a, _n=t["name"], _p=manifest["name"]: f"{_p}: {_n}", read_only=not risk,
                                needs_screen=bool(t.get("needs_screen", False))))
        return out
    return build


def rest_def(cfg: dict) -> PluginDef:
    pid = "rest-" + cfg["id"]
    fields = []
    if cfg.get("auth") in ("bearer", "header", "basic"):
        fields.append({"key": "token", "label": {"bearer": "Bearer token", "header": "Header value", "basic": "Password"}[cfg["auth"]], "secret": True, "required": True})

    def build(c: Conn) -> list[ToolSpec]:
        base = cfg["base_url"].rstrip("/")

        def req(ctx: ToolContext, a: dict) -> Any:
            method = a.get("method", "GET").upper()
            if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
                raise ConnectorError("Unsupported method.")
            path = "/" + str(a.get("path", "")).lstrip("/")
            if path.startswith("//") or "://" in path:
                raise ConnectorError("Give a path on the configured API, not a full URL.")
            headers = dict(a.get("headers") or {})
            tok = c.cfg("token")
            if cfg.get("auth") == "bearer":
                headers["Authorization"] = f"Bearer {tok}"
            elif cfg.get("auth") == "header":
                headers[cfg.get("header_name") or "X-API-Key"] = tok
            elif cfg.get("auth") == "basic":
                headers["Authorization"] = "Basic " + base64.b64encode(f"{cfg.get('username', '')}:{tok}".encode()).decode()
            return _res(c.http(ctx, method, base + path, headers=headers, params=a.get("query"), json_body=a.get("body")), cfg["name"])

        def risk(ctx: ToolContext, a: dict) -> Risk | None:
            m = a.get("method", "GET").upper()
            if m in ("GET", "HEAD"):
                return None
            if not cfg.get("allow_writes", False):
                return Risk("write", f"{m} {cfg['name']}{a.get('path')}", {"pattern": f"{m} {a.get('path')}", "body": short(json.dumps(a.get("body")), 800)})
            return None

        return [ToolSpec(safe_name(f"rest_{cfg['id']}_request"), f"Call the {cfg['name']} REST API ({base}). {cfg.get('description', '')}",
                         obj({"method": s("GET, POST, PUT, PATCH or DELETE"), "path": s("Path starting with /"), "query": {"type": "object"}, "body": {"type": "object"},
                              "headers": {"type": "object"}}, ["method", "path"]), lambda ctx, a: req(ctx, a), risk=risk,
                         label=lambda a: f"{cfg['name']}: {a.get('method', 'GET')} {a.get('path', '')}")]
    return PluginDef(pid, f"REST: {cfg['name']}", cfg.get("description") or f"Generic REST connector for {cfg['base_url']}", kind="rest", fields=fields, build=build,
                     help="Calls a JSON REST API. Only GET is free; other methods ask for approval unless you enabled writes.")


# ----------------------------------------------------------------------------- manager
class PluginManager:
    def __init__(self, engine: "Engine"):
        self.engine = engine
        self.db, self.settings, self.admin = engine.db, engine.settings, engine.admin
        self.google = GoogleOAuth()
        self.defs: dict[str, PluginDef] = {}
        self.reload()

    # -- registry -------------------------------------------------------------
    def _sync_bundled_plugins(self) -> None:
        """Copy newer bundled plugin files over stale copies in the data dir.

        Deploys used to leave the installed copy untouched, so tools that
        shipped in the repo never appeared in the running app. A bundled file
        only wins when it is newer than the installed one, so edits made on
        the box after a deploy are preserved.
        """
        root = paths.resource_root() / "plugins"
        dest_root = paths.plugins_dir()
        if not root.is_dir() or not dest_root.is_dir():
            return
        for src_dir in sorted(root.iterdir()):
            mf = src_dir / "plugin.json"
            if not mf.is_file():
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
                pid = re.sub(r"[^a-z0-9_-]", "-", str(m.get("id", "")).lower())
                dest = dest_root / pid
                if not pid or not dest.is_dir():
                    continue
                changed = False
                for f in src_dir.iterdir():
                    if not f.is_file() or f.suffix not in {".py", ".json", ".md", ".txt"}:
                        continue
                    target = dest / f.name
                    if not target.is_file() or f.stat().st_mtime > target.stat().st_mtime + 1:
                        shutil.copy2(f, target)
                        changed = True
                if changed:
                    shutil.rmtree(dest / "__pycache__", ignore_errors=True)
                    self.engine.log.info("Synced bundled plugin %s from %s", pid, src_dir)
            except Exception as e:  # noqa: BLE001
                self.engine.log.warning("Could not sync bundled plugin %s: %s", src_dir.name, e)

    def reload(self) -> None:
        self._sync_bundled_plugins()
        defs = {d.id: d for d in builtin_defs()}
        for cfg in self.settings.get("rest_connectors", []) or []:
            d = rest_def(cfg)
            defs[d.id] = d
        for folder in sorted(paths.plugins_dir().iterdir()) if paths.plugins_dir().exists() else []:
            mf = folder / "plugin.json"
            if not mf.is_file():
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
                d = self._def_from_manifest(m, folder)
                defs[d.id] = d
            except Exception as e:  # noqa: BLE001
                self.engine.log.warning("Bad plugin in %s: %s", folder, e)
        self.defs = defs

    def _def_from_manifest(self, m: dict, folder: Path) -> PluginDef:
        pid = re.sub(r"[^a-z0-9_-]", "-", str(m["id"]).lower())
        # Stale installs: an older copy in the data dir may lack the oauth/setup
        # links that the bundled manifest already ships. Bundled installs always
        # adopt the bundled sign-in block (oauth endpoints migrate, e.g. HubSpot
        # v1 -> versioned token URL); other sources only get gaps filled.
        try:
            bundled = paths.resource_root() / "plugins" / pid / "plugin.json"
            if bundled.is_file():
                bm = json.loads(bundled.read_text(encoding="utf-8"))
                src = self.db.scalar("SELECT source FROM plugins WHERE id=?", (pid,), "local") or "local"
                if src.startswith("bundled:"):
                    for k in ("oauth", "setup_url", "setup_label", "help"):
                        if bm.get(k):
                            m[k] = bm[k]
                else:
                    for k in ("oauth", "setup_url", "setup_label"):
                        if bm.get(k) and not m.get(k):
                            m[k] = bm[k]
                    if bm.get("help") and not m.get("help"):
                        m["help"] = bm["help"]
        except Exception:  # noqa: BLE001
            pass
        kind = "python" if m.get("entry") else "declarative"
        build = build_python(folder, {**m, "id": pid}) if kind == "python" else build_declarative({**m, "id": pid})
        return PluginDef(pid, m.get("name", pid), m.get("description", ""), kind=kind, version=m.get("version", "1.0.0"), fields=m.get("fields", []),
                         build=build, path=folder, source=(self.db.scalar("SELECT source FROM plugins WHERE id=?", (pid,), "local") or "local"),
                         skills=m.get("skills", []), help=m.get("help", ""), oauth=m.get("oauth"),
                         setup_url=m.get("setup_url", "") or "", setup_label=m.get("setup_label", "") or "")

    # -- config ----------------------------------------------------------------
    def config_value(self, pid: str, key: str, owner: str = "") -> str:
        d = self.defs.get(pid)
        field_ = next((f for f in (d.fields if d else []) if f["key"] == key), None)
        if (field_ and field_.get("secret")) or key == "refresh_token":
            # Secret credentials are owner-scoped when the acting Bot's owner is
            # known (their own Connect sign-in), with the legacy server-wide
            # value as fallback so older connects keep working.
            return _cget(pid, owner, key)
        return str((self.settings.get(f"plugin_cfg.{pid}", {}) or {}).get(key, "") or (field_ or {}).get("default", ""))

    def set_config(self, pid: str, values: dict[str, str]) -> None:
        d = self.defs.get(pid)
        if not d:
            raise ValueError("Unknown plugin.")
        cfg = dict(self.settings.get(f"plugin_cfg.{pid}", {}) or {})
        for f in d.fields:
            k = f["key"]
            if k not in values:
                continue
            v = str(values[k]).strip()
            if f.get("secret"):
                if v:
                    secrets.set_secret(f"plugin:{pid}:{k}", v)
            else:
                cfg[k] = v
        self.settings.set(f"plugin_cfg.{pid}", cfg)

    def clear_secrets(self, pid: str, owner: str = "") -> None:
        d = self.defs.get(pid)
        for f in (d.fields if d else []):
            if f.get("secret"):
                secrets.delete_secret(_ckey(pid, owner, f["key"]))
        secrets.delete_secret(_ckey(pid, owner, "refresh_token"))

    def configured(self, pid: str, owner: str = "") -> bool:
        d = self.defs.get(pid)
        if not d:
            return False
        rt = _cget(pid, owner, "refresh_token")
        tok = self.config_value(pid, "token")
        for f in d.fields:
            if f.get("required") and not self.config_value(pid, f["key"]):
                if (d.oauth or {}).get("provider") == "google" and f["key"] in ("client_id", "client_secret"):
                    gcid, gcsec = google_client(self, pid)
                    if gcid and gcsec:
                        continue   # the server's Google app (or Gmail's client) covers this
                if f["key"] == "token" and rt and self.config_value(pid, "client_id"):
                    continue   # signed in via Connect (OAuth) instead of a pasted token
                if pid == "zoom" and f["key"] in ("client_id", "client_secret") and rt:
                    continue
                return False
        if d.oauth and not rt:
            # Alternative credential paths that need no browser sign-in.
            if tok:
                pass   # pasted PAT / bearer / private-app token (asana, hubspot, x)
            elif pid == "zoom" and self.config_value(pid, "account_id"):
                pass   # Server-to-Server OAuth path
            elif pid == "salesforce" and self.config_value(pid, "username") and self.config_value(pid, "password"):
                pass   # username-password OAuth flow
            elif pid == "onshape" and self.config_value(pid, "access_key") and self.config_value(pid, "secret_key"):
                pass   # API key pair
            else:
                return False
        return True

    def info(self, pid: str, owner: str = "") -> dict:
        d = self.defs[pid]
        specs = []
        try:
            specs = d.build(Conn(self, d)) if d.build else []
        except Exception:
            specs = []
        # Google plugins ride on the server's Google app (same client as
        # "Continue with Google"): no client id to paste and no Cloud Console
        # detour. Fields stay in the def so a custom client can still be set
        # through the desktop app / API, they are just not advertised here.
        managed_google = False
        if (d.oauth or {}).get("provider") == "google":
            gcid, gcsec = google_client(self, d.id)
            managed_google = bool(gcid and gcsec)
        fields = [] if managed_google else d.fields
        setup_url = "" if managed_google else d.setup_url
        setup_label = "" if managed_google else (d.setup_label or "Open setup")
        help_text = d.help
        if managed_google:
            help_text = ("One tap: Connect opens Google's sign-in with this server's Google app, so there is no "
                         "client ID or secret to paste. Use the Google account that owns the Gmail/Calendar/Drive data.")
        return {"id": d.id, "name": d.name, "description": d.description, "kind": d.kind, "version": d.version, "help": help_text,
                "fields": [{**f, "set": bool(self.config_value(d.id, f["key"])), "value": ("" if f.get("secret") else self.config_value(d.id, f["key"]))} for f in fields],
                "oauth": bool(d.oauth), "connected": bool(not d.oauth or _cget(d.id, owner, "refresh_token")),
                "managed": managed_google,
                "setup_url": setup_url, "setup_label": setup_label,
                "configured": self.configured(d.id, owner), "allowed": plugin_allowed(self.admin, d.id), "installed": d.kind != "builtin" and d.kind != "rest",
                "source": d.source, "tools": [{"name": t.name, "description": t.description, "read_only": t.read_only or t.risk is None} for t in specs],
                "removable": d.kind in ("declarative", "python", "rest")}

    def list(self, owner: str = "") -> list[dict]:
        return [self.info(pid, owner=owner) for pid in self.defs]

    # -- tools for a Bot ---------------------------------------------------------
    def tool_specs(self, granted: set[str], owner: str = "") -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for pid, d in self.defs.items():
            if f"plugin:{pid}" not in granted or not plugin_allowed(self.admin, pid) or not d.build or not self.configured(pid, owner):
                continue
            try:
                for t in d.build(Conn(self, d, owner=owner)):
                    t.group = f"plugin:{pid}"
                    out.append(t)
            except Exception as e:  # noqa: BLE001
                self.engine.log.warning("Plugin %s failed to build tools: %s", pid, e)
        return out

    def test(self, pid: str) -> str:
        d = self.defs.get(pid)
        if not d:
            raise ValueError("Unknown plugin.")
        if not self.configured(pid):
            raise ConnectorError("Not fully configured yet." + (" Press Connect to sign in." if d.oauth else ""))
        if not d.check:
            return "Configured (this plugin has no connection test)."
        return d.check(Conn(self, d))

    # -- OAuth ----------------------------------------------------------------------
    @property
    def pending(self) -> dict[str, dict]:
        """In-flight sign-in requests (state -> request), used by the callbacks."""
        return self.google.pending

    def begin_oauth(self, pid: str, redirect_uri: str, mobile: bool = False, owner: str = "") -> str:
        d = self.defs.get(pid)
        if not d or not d.oauth:
            raise ValueError("This plugin does not use sign-in.")
        c = Conn(self, d)
        cid, csec = google_client(self, pid)
        if not cid:
            cid = c.cfg("client_id")
        if not cid:
            raise ConnectorError("Enter the OAuth client ID and secret first.")
        if (d.oauth or {}).get("provider") == "google" and not csec:
            raise ConnectorError("This Google sign-in needs the client secret too. Add it in Plugins (desktop) or set it for the server's Google app.")
        o = dict(d.oauth)
        auth_url, token_url = o.get("auth_url") or GoogleOAuth.AUTH, o.get("token_url") or GoogleOAuth.TOKEN
        if pid == "salesforce":  # sandbox vs production login host
            base = (c.cfg("login_url") or "https://login.salesforce.com").rstrip("/")
            auth_url, token_url = base + "/services/oauth2/authorize", base + "/services/oauth2/token"
        if pid == "teams":  # tenant-specific Microsoft endpoints
            tenant = (c.cfg("tenant") or "common").strip() or "common"
            auth_url = (o.get("auth_url") or "").replace("common", tenant)
            token_url = (o.get("token_url") or "").replace("common", tenant)
        # Region/account overrides (e.g. Sage token host, self-hosted vendors):
        # a plugin may expose optional auth_url/token_url config fields.
        auth_url = c.cfg("auth_url") or auth_url
        token_url = c.cfg("token_url") or token_url
        return self.google.auth_url(pid, cid, o["scopes"], redirect_uri, auth_url=auth_url, token_url=token_url,
                                    secret_style=o.get("secret_style", "body"), extra=o.get("extra_auth"),
                                    mobile=mobile, owner=owner)

    def finish_oauth(self, state: str, code: str) -> str:
        p = self.google.pending.get(state)
        if not p:
            raise ConnectorError("Unknown or expired sign-in request.")
        c = Conn(self, self.defs[p["plugin"]])
        d = self.defs[p["plugin"]]
        cid, csec = google_client(self, p["plugin"])
        if not cid:
            cid, csec = c.cfg("client_id"), c.cfg("client_secret")
        return self.google.exchange(state, code, cid, csec, token_url=p.get("token_url"), secret_style=p.get("secret_style", "body"),
                                    owner=str(p.get("owner") or ""))

    # -- install / uninstall -----------------------------------------------------------
    def catalog(self) -> list[dict]:
        entries: list[dict] = []
        cat = paths.resource_root() / "plugins" / "catalog.json"
        if cat.is_file():
            try:
                entries += json.loads(cat.read_text(encoding="utf-8")).get("plugins", [])
            except ValueError:
                pass
        import httpx
        for url in self.settings.get("catalog_urls", []) or []:
            try:
                ok, _ = self.engine.net.check(None, url)
                if ok:
                    entries += httpx.get(url, timeout=10).json().get("plugins", [])
            except Exception:
                continue
        installed = set(self.defs)
        for e in entries:
            e["installed"] = e.get("id") in installed
        return entries

    def install(self, source: str) -> dict:
        if not self.admin.plugin_install_allowed():
            raise ValueError("Your administrator has disabled plugin installation.")
        source = source.strip()
        tmp = paths.data_dir() / "tmp-plugin-install"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            if source.startswith("bundled:"):
                src = (paths.resource_root() / source.split(":", 1)[1]).resolve()
                if not src.is_relative_to((paths.resource_root() / "plugins").resolve()):
                    raise ValueError("Invalid bundled source.")
                shutil.copytree(src, tmp)
            elif re.match(r"^(https?://|git@|ssh://)", source):
                if not shutil.which("git"):
                    raise ValueError("git is not installed or not on PATH.")
                r = subprocess.run(["git", "clone", "--depth", "1", source, str(tmp)], capture_output=True, text=True, timeout=180,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode != 0:
                    raise ValueError("git clone failed: " + short(r.stderr, 300))
                shutil.rmtree(tmp / ".git", ignore_errors=True)
            else:
                src = Path(source).expanduser()
                if not src.is_dir():
                    raise ValueError(f"{source} is not a folder, bundled source or git URL.")
                shutil.copytree(src, tmp)
            mf = tmp / "plugin.json"
            if not mf.is_file():
                raise ValueError("plugin.json not found at the top of the plugin folder.")
            m = json.loads(mf.read_text(encoding="utf-8"))
            pid = re.sub(r"[^a-z0-9_-]", "-", str(m.get("id", "")).lower())
            if not pid or pid in {d.id for d in builtin_defs()}:
                raise ValueError("Plugin needs a unique 'id' that does not clash with a built-in connector.")
            if not plugin_allowed(self.admin, pid):
                raise ValueError(f"Your administrator has not allowed the plugin '{pid}'.")
            dest = paths.plugins_dir() / pid
            shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(tmp), str(dest))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.db.execute("INSERT INTO plugins(id,source,path,enabled,installed_at,version) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET source=excluded.source, path=excluded.path, version=excluded.version",
                        (pid, source, str(dest), 1, time.time(), m.get("version", "")))
        for sk in m.get("skills", []):  # ship skills along with the plugin (as drafts to review)
            f = dest / sk
            if f.is_file():
                name = f"{pid}-{f.stem}"
                self.engine.skills.save(name, f.read_text(encoding="utf-8"), status="draft")
        self.reload()
        self.engine.events.publish("plugins", change="installed", plugin_id=pid)
        return self.info(pid)

    def uninstall(self, pid: str) -> None:
        d = self.defs.get(pid)
        if not d:
            return
        if d.kind == "rest":
            self.settings.set("rest_connectors", [c for c in self.settings.get("rest_connectors", []) if "rest-" + c["id"] != pid])
        elif d.path and d.path.is_relative_to(paths.plugins_dir()):
            shutil.rmtree(d.path, ignore_errors=True)
            self.db.execute("DELETE FROM plugins WHERE id=?", (pid,))
        self.clear_secrets(pid)
        # Owner-scoped credentials too: every owner who connected this plugin.
        try:
            for r in self.engine.db.query("SELECT DISTINCT owner FROM bots WHERE owner!=''"):
                self.clear_secrets(pid, owner=str(r["owner"]))
        except Exception:  # noqa: BLE001
            pass
        self.reload()
        self.engine.events.publish("plugins", change="removed", plugin_id=pid)

    def add_rest(self, cfg: dict) -> dict:
        if not cfg.get("name") or not cfg.get("base_url", "").startswith(("http://", "https://")):
            raise ValueError("A REST connector needs a name and an http(s) base URL.")
        cfg = dict(cfg)
        cfg["id"] = cfg.get("id") or re.sub(r"[^a-z0-9]+", "-", cfg["name"].lower()).strip("-") or pysecrets.token_hex(3)
        lst = [c for c in self.settings.get("rest_connectors", []) if c["id"] != cfg["id"]] + [cfg]
        self.settings.set("rest_connectors", lst)
        self.reload()
        return self.info("rest-" + cfg["id"])
