"""Onshape plugin: CAD documents, workspaces, elements (parts/assemblies/drawings).

Two ways in: press Connect in Plugins and approve in the browser (OAuth,
refreshes itself), or paste an API Access Key + Secret Key (no sign-in dance).
"""
from __future__ import annotations

import base64

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None


def _base(api) -> str:
    ver = ((api.config("api_version", "") or "").strip() or "v9").lstrip("/")
    return f"https://cad.onshape.com/api/{ver}"


def _auth(api) -> dict:
    cid, csec = (api.config("client_id") or "").strip(), (api.config("client_secret") or "").strip()
    try:
        import core.plugins as _plug
        rt = _plug.secrets.get_secret("plugin:onshape:refresh_token")
    except Exception:  # noqa: BLE001
        rt = ""
    if cid and csec and rt:  # browser Connect sign-in (OAuth)
        # Per the Onshape OAuth docs the client id/secret go in the POST body.
        data = oauth2_token(None, "onshape", "https://oauth.onshape.com/oauth/token",
                            {"grant_type": "refresh_token", "refresh_token": rt,
                             "client_id": cid, "client_secret": csec})
        return {"Authorization": f"Bearer {data['access_token']}", "Accept": "application/json"}
    key, sec = (api.config("access_key") or "").strip(), api.config("secret_key")
    if not (key and sec):
        raise RuntimeError("Onshape is not connected: press Connect in Plugins and approve in the browser, or paste the Access Key and Secret Key.")
    basic = base64.b64encode(f"{key}:{sec}".encode()).decode()
    return {"Authorization": f"Basic {basic}", "Accept": "application/json"}


def documents(ctx, args, api):
    return api.http(ctx, "GET", f"{_base(api)}/documents", headers=_auth(api), params={"limit": 20})


def document(ctx, args, api):
    return api.http(ctx, "GET", f"{_base(api)}/documents/{args['document_id']}", headers=_auth(api))


def workspaces(ctx, args, api):
    return api.http(ctx, "GET", f"{_base(api)}/documents/{args['document_id']}/workspaces", headers=_auth(api))


def elements(ctx, args, api):
    return api.http(ctx, "GET",
                    f"{_base(api)}/documents/d/{args['document_id']}/w/{args['workspace_id']}/elements",
                    headers=_auth(api))


def document_create(ctx, args, api):
    body = {"name": args["name"]}
    if args.get("description"):
        body["description"] = args["description"]
    return api.http(ctx, "POST", f"{_base(api)}/documents", headers=_auth(api), json_body=body)


def register(api):
    return [
        {"name": "documents", "description": "List Onshape documents you can access (id, name, modified).",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: documents(ctx, a, api)},
        {"name": "document", "description": "Details of one document (owner, workspaces, default tab).",
         "input_schema": {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"]},
         "handler": lambda ctx, a: document(ctx, a, api)},
        {"name": "workspaces", "description": "Workspaces (branches) of a document, with their ids.",
         "input_schema": {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"]},
         "handler": lambda ctx, a: workspaces(ctx, a, api)},
        {"name": "elements", "description": "Tabs of a workspace: parts, assemblies, drawings (names, ids, types).",
         "input_schema": {"type": "object", "properties": {
             "document_id": {"type": "string"}, "workspace_id": {"type": "string"}}, "required": ["document_id", "workspace_id"]},
         "handler": lambda ctx, a: elements(ctx, a, api)},
        {"name": "create_document", "description": "Create a new empty Onshape document.",
         "input_schema": {"type": "object", "properties": {
             "name": {"type": "string"}, "description": {"type": "string"}}, "required": ["name"]},
         "handler": lambda ctx, a: document_create(ctx, a, api), "risk": "write"},
    ]
