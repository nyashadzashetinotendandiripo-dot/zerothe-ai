"""Microsoft Teams plugin: chats, channel messages and online meetings via Microsoft Graph.

Pairing uses the device-code flow, driven by two tools so the user approves on their
own screen in between: connect_start shows a code + link, connect_finish (called after
the user approves) stores the sign-in. Tokens refresh themselves afterwards.
"""
from __future__ import annotations

import time

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

try:
    from core import secrets as _secrets
except ImportError:  # pragma: no cover
    _secrets = None

SCOPES = "offline_access openid profile User.Read Chat.ReadWrite Chat.Create OnlineMeetings.ReadWrite"
GRAPH = "https://graph.microsoft.com/v1.0"

_PENDING: dict[str, dict] = {}


def _tenant(api) -> str:
    return ((api.config("tenant", "") or "").strip() or "common")


def _form(url: str, data: dict):
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "ZerotheBot"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, _json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, _json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def _client(api) -> str:
    cid = (api.config("client_id") or "").strip()
    if not cid:
        raise RuntimeError("Teams is not configured: paste the Application (client) ID in Plugins, then press Connect.")
    return cid


def connect_start(ctx, args, api):
    cid = _client(api)
    status, data = _form(f"https://login.microsoftonline.com/{_tenant(api)}/oauth2/v2.0/devicecode",
                         {"client_id": cid, "scope": SCOPES})
    if status >= 400 or not data.get("user_code"):
        raise RuntimeError(f"Microsoft would not start sign-in (HTTP {status}). Check the client ID. {str(data)[:200]}")
    _PENDING[ctx.bot["id"] if ctx else ""] = {"device_code": data["device_code"], "until": time.time() + int(data.get("expires_in", 900)) - 30,
                                              "interval": int(data.get("interval", 5) or 5)}
    url = data.get("verification_uri") or "https://microsoft.com/devicelogin"
    return (f"Ask the user to open {url} and enter the code {data['user_code']} (expires in ~15 minutes). "
            f"When they confirm it is done, call teams_connect_finish.")


def connect_finish(ctx, args, api):
    cid = _client(api)
    bid = ctx.bot["id"] if ctx else ""
    pend = _PENDING.get(bid)
    if not pend:
        return "No sign-in is in progress. Call teams_connect_start first."
    deadline = min(pend["until"], time.time() + 75)
    while time.time() < deadline:
        status, data = _form(f"https://login.microsoftonline.com/{_tenant(api)}/oauth2/v2.0/token",
                             {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                              "client_id": cid, "device_code": pend["device_code"]})
        if data.get("access_token"):
            rt = data.get("refresh_token", "")
            if rt and _secrets is not None:
                try:
                    _secrets.set_secret("plugin:teams:refresh_token", rt)
                except Exception:
                    pass
            _PENDING.pop(bid, None)
            try:
                from core.plugins import _OAUTH2_CACHE
                _OAUTH2_CACHE.pop("teams", None)
            except Exception:
                pass
            who = data.get("id_token_claims", {})
            return "Teams is paired and remembered." + (f" Signed in as {who.get('preferred_username', '')}." if who.get("preferred_username") else "")
        err = (data or {}).get("error", "")
        if err in ("expired_token",):
            _PENDING.pop(bid, None)
            return "The code expired. Call teams_connect_start again for a fresh code."
        if err not in ("authorization_pending", "slow_down", "authorization_declined"):
            return f"Sign-in failed: {err or status}. Check the app registration and try teams_connect_start again."
        if err == "authorization_declined":
            _PENDING.pop(bid, None)
            return "The user declined the sign-in. Call teams_connect_start again if that was a mistake."
        time.sleep(pend["interval"])
    return "Still waiting for approval. Ask the user to finish approving in the browser, then call teams_connect_finish again."


def _auth(api) -> dict:
    cid = _client(api)
    rt = (api.config("refresh_token") or "").strip()
    if not rt:
        raise RuntimeError("Teams is not paired yet: press Connect in Plugins and approve in the browser, or run teams_connect_start then teams_connect_finish.")
    payload = {"grant_type": "refresh_token", "client_id": cid, "refresh_token": rt, "scope": SCOPES}
    sec = (api.config("client_secret") or "").strip()
    if sec:  # confidential app registrations must send the secret on refresh
        payload["client_secret"] = sec
    data = oauth2_token(None, "teams", f"https://login.microsoftonline.com/{_tenant(api)}/oauth2/v2.0/token", payload)
    return {"Authorization": f"Bearer {data['access_token']}"}


def chats_list(ctx, args, api):
    return api.http(ctx, "GET", f"{GRAPH}/me/chats", headers=_auth(api), params={"$top": 25})


def chat_messages(ctx, args, api):
    return api.http(ctx, "GET", f"{GRAPH}/me/chats/{args['chat_id']}/messages", headers=_auth(api),
                    params={"$top": int(args.get("limit", 20) or 20)})


def chat_send(ctx, args, api):
    return api.http(ctx, "POST", f"{GRAPH}/me/chats/{args['chat_id']}/messages", headers=_auth(api),
                    json_body={"body": {"contentType": "text", "content": args["text"]}})


def channel_send(ctx, args, api):
    return api.http(ctx, "POST", f"{GRAPH}/teams/{args['team_id']}/channels/{args['channel_id']}/messages",
                    headers=_auth(api), json_body={"body": {"contentType": "text", "content": args["text"]}})


def my_teams(ctx, args, api):
    return api.http(ctx, "GET", f"{GRAPH}/me/joinedTeams", headers=_auth(api))


def meeting_create(ctx, args, api):
    return api.http(ctx, "POST", f"{GRAPH}/me/onlineMeetings", headers=_auth(api),
                    json_body={"startDateTime": args["start"], "endDateTime": args["end"], "subject": args.get("subject", "Meeting")})


def register(api):
    return [
        {"name": "connect_start", "description": "Start Teams pairing: returns a code + link for the user to approve in their browser.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: connect_start(ctx, a, api)},
        {"name": "connect_finish", "description": "Finish Teams pairing after the user approves (call again if still waiting).",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: connect_finish(ctx, a, api)},
        {"name": "chats", "description": "List Teams chats (id, topic, members).",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: chats_list(ctx, a, api)},
        {"name": "chat_messages", "description": "Recent messages in a chat.",
         "input_schema": {"type": "object", "properties": {"chat_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["chat_id"]},
         "handler": lambda ctx, a: chat_messages(ctx, a, api)},
        {"name": "chat_send", "description": "Send a message to a one-to-one or group chat.",
         "input_schema": {"type": "object", "properties": {"chat_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["chat_id", "text"]},
         "handler": lambda ctx, a: chat_send(ctx, a, api), "risk": "send"},
        {"name": "channel_send", "description": "Post a message to a team channel.",
         "input_schema": {"type": "object", "properties": {
             "team_id": {"type": "string"}, "channel_id": {"type": "string"}, "text": {"type": "string"}},
             "required": ["team_id", "channel_id", "text"]},
         "handler": lambda ctx, a: channel_send(ctx, a, api), "risk": "send"},
        {"name": "my_teams", "description": "Teams (and their ids) this user belongs to, for channel_send.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: my_teams(ctx, a, api)},
        {"name": "create_meeting", "description": "Schedule an online meeting. Times like 2026-10-20T14:00:00.",
         "input_schema": {"type": "object", "properties": {
             "subject": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}},
             "required": ["start", "end"]},
         "handler": lambda ctx, a: meeting_create(ctx, a, api), "risk": "write"},
    ]
