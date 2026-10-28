"""Zoom plugin: meetings and recordings.

Two ways in: press Connect in Plugins and approve in the browser (OAuth user
sign-in, refreshes itself), or paste the three values from a
marketplace.zoom.us Server-to-Server OAuth app (no browser dance).
"""
from __future__ import annotations

import base64

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

API = "https://api.zoom.us/v2"


def _auth(api) -> dict:
    cid, csec = (api.config("client_id") or "").strip(), api.config("client_secret")
    acct = (api.config("account_id") or "").strip()
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode() if (cid and csec) else ""
    try:
        import core.plugins as _plug
        rt = _plug.secrets.get_secret("plugin:zoom:refresh_token")
    except Exception:  # noqa: BLE001
        rt = ""
    if cid and csec and rt:  # browser Connect sign-in (OAuth user flow)
        data = oauth2_token(None, "zoom", "https://zoom.us/oauth/token",
                            {"grant_type": "refresh_token", "refresh_token": rt},
                            headers={"Authorization": f"Basic {basic}"})
        return {"Authorization": f"Bearer {data['access_token']}"}
    if not (cid and csec and acct):
        raise RuntimeError("Zoom is not connected: press Connect in Plugins and approve in the browser, or paste the Client ID, secret and Account ID.")
    data = oauth2_token(None, "zoom", "https://zoom.us/oauth/token",
                        {"grant_type": "account_credentials", "account_id": acct},
                        headers={"Authorization": f"Basic {basic}"}, rotate_field=None)
    return {"Authorization": f"Bearer {data['access_token']}"}


def meetings_list(ctx, args, api):
    return api.http(ctx, "GET", f"{API}/users/{args.get('user_id') or 'me'}/meetings",
                    headers=_auth(api), params={"type": "upcoming", "page_size": 30})


def meeting_get(ctx, args, api):
    return api.http(ctx, "GET", f"{API}/meetings/{args['meeting_id']}", headers=_auth(api))


def meeting_create(ctx, args, api):
    body = {"topic": args["topic"], "type": 2, "start_time": args["start_time"],
            "duration": int(args.get("duration", 30) or 30)}
    if args.get("agenda"):
        body["agenda"] = args["agenda"]
    return api.http(ctx, "POST", f"{API}/users/me/meetings", headers=_auth(api), json_body=body)


def recordings_list(ctx, args, api):
    params = {"page_size": 30}
    if args.get("from"):
        params["from"] = args["from"]
    if args.get("to"):
        params["to"] = args["to"]
    return api.http(ctx, "GET", f"{API}/users/{args.get('user_id') or 'me'}/recordings",
                    headers=_auth(api), params=params)


def register(api):
    return [
        {"name": "meetings", "description": "List upcoming Zoom meetings (id, topic, start time, join URL).",
         "input_schema": {"type": "object", "properties": {"user_id": {"type": "string", "description": "Email or 'me'"}}},
         "handler": lambda ctx, a: meetings_list(ctx, a, api)},
        {"name": "meeting", "description": "Details of one meeting including join URL and password.",
         "input_schema": {"type": "object", "properties": {"meeting_id": {"type": "string", "description": "Meeting id"}}, "required": ["meeting_id"]},
         "handler": lambda ctx, a: meeting_get(ctx, a, api)},
        {"name": "create_meeting", "description": "Schedule a Zoom meeting. Start time looks like 2026-10-20T14:00:00.",
         "input_schema": {"type": "object", "properties": {
             "topic": {"type": "string"}, "start_time": {"type": "string", "description": "e.g. 2026-10-20T14:00:00"},
             "duration": {"type": "integer", "description": "Minutes, default 30"},
             "agenda": {"type": "string"}}, "required": ["topic", "start_time"]},
         "handler": lambda ctx, a: meeting_create(ctx, a, api), "risk": "write"},
        {"name": "recordings", "description": "List cloud recordings, optionally between from/to dates (YYYY-MM-DD).",
         "input_schema": {"type": "object", "properties": {"user_id": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}}},
         "handler": lambda ctx, a: recordings_list(ctx, a, api)},
    ]
