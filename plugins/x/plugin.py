"""X (Twitter) plugin: profiles, posts, search and posting.

Two auth paths, both Bearer on the wire:
- Pasted Bearer Token (reads; whatever your X tier allows).
- Connect sign-in (OAuth2 PKCE): refreshes itself and unlocks posting.
"""
from __future__ import annotations

import base64

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

API = "https://api.x.com/2"


def _auth(api) -> dict:
    cid = (api.config("client_id") or "").strip()
    if cid:
        import core.plugins as _plug
        rt = _plug.secrets.get_secret("plugin:x:refresh_token")
        if rt:
            sec = (api.config("client_secret") or "").strip()
            if sec:  # confidential app: client credentials in the Basic header
                headers = {"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()}
                payload = {"grant_type": "refresh_token", "refresh_token": rt}
            else:  # public app (PKCE, no secret): client id goes in the body
                headers = None
                payload = {"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid}
            data = oauth2_token(None, "x", "https://api.x.com/2/oauth2/token", payload, headers=headers)
            return {"Authorization": f"Bearer {data['access_token']}"}
    tok = (api.config("token") or "").strip()
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    raise RuntimeError("X is not connected: paste a Bearer Token, or paste the Client ID and press Connect.")


def me(ctx, args, api):
    return api.http(ctx, "GET", f"{API}/users/me", headers=_auth(api))


def user_by_name(ctx, args, api):
    return api.http(ctx, "GET", f"{API}/users/by/username/{args['username'].lstrip('@')}", headers=_auth(api),
                    params={"user.fields": "name,description,public_metrics,verified"})


def user_posts(ctx, args, api):
    return api.http(ctx, "GET", f"{API}/users/{args['id']}/tweets", headers=_auth(api),
                    params={"max_results": 10, "tweet.fields": "created_at,public_metrics"})


def search(ctx, args, api):
    return api.http(ctx, "GET", f"{API}/tweets/search/recent", headers=_auth(api),
                    params={"query": args["query"], "max_results": 10, "tweet.fields": "created_at,public_metrics,author_id"})


def post(ctx, args, api):
    text = (args.get("text") or "").strip()
    if not text:
        raise RuntimeError("Empty post.")
    if len(text) > 280:
        raise RuntimeError(f"That is {len(text)} characters; X allows 280.")
    return api.http(ctx, "POST", f"{API}/tweets", headers=_auth(api), json_body={"text": text})


def register(api):
    return [
        {"name": "me", "description": "The authenticated X user (id, name, username). Also proves the token works.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: me(ctx, a, api)},
        {"name": "user_by_name", "description": "Look up an X user by username (without the @), with follower counts and bio.",
         "input_schema": {"type": "object", "properties": {"username": {"type": "string"}}, "required": ["username"]},
         "handler": lambda ctx, a: user_by_name(ctx, a, api)},
        {"name": "user_posts", "description": "Recent posts by a user id (get it from x_user_by_name first).",
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
         "handler": lambda ctx, a: user_posts(ctx, a, api)},
        {"name": "search", "description": "Search recent posts (last 7 days on most tiers). Supports from:, -is:retweet, lang: operators.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
         "handler": lambda ctx, a: search(ctx, a, api)},
        {"name": "post", "description": "Post a tweet (max 280 characters). Needs the Connect sign-in; bearer-only tokens cannot post.",
         "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
         "handler": lambda ctx, a: post(ctx, a, api), "risk": "send"},
    ]
