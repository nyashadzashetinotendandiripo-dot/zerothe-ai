"""Salesforce plugin: SOQL queries plus read/create/update on any object.

Two ways in: press Connect in Plugins and approve in the browser (OAuth web
flow, refreshes itself), or paste the username and password+security-token too
for the password flow (no browser dance).
"""
from __future__ import annotations

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

VERSION = "v60.0"


def _session(api) -> tuple[str, dict]:
    base = (api.config("login_url", "") or "").strip() or "https://login.salesforce.com"
    user, pw = api.config("username"), api.config("password")
    cid, csec = api.config("client_id"), api.config("client_secret")
    if not (cid and csec):
        raise RuntimeError("Salesforce is not configured: paste the Consumer Key and Secret in Plugins, then press Connect.")
    try:
        import core.plugins as _plug
        rt = _plug.secrets.get_secret("plugin:salesforce:refresh_token")
    except Exception:  # noqa: BLE001
        rt = ""
    if rt:  # browser Connect sign-in (OAuth web flow)
        payload = {"grant_type": "refresh_token", "client_id": cid, "client_secret": csec,
                   "refresh_token": rt}
        try:
            from core.plugins import stored_redirect_uri as _redir
            if _redir("salesforce"):
                payload["redirect_uri"] = _redir("salesforce")
        except ImportError:  # pragma: no cover
            pass
        data = oauth2_token(None, "salesforce", base.rstrip("/") + "/services/oauth2/token", payload)
    else:  # username-password flow (no browser dance)
        if not (user and pw):
            raise RuntimeError("Salesforce is not connected: press Connect in Plugins and approve in the browser, or paste the username and password+security-token too.")
        data = oauth2_token(None, "salesforce", base.rstrip("/") + "/services/oauth2/token",
                            {"grant_type": "password", "client_id": cid, "client_secret": csec,
                             "username": user, "password": pw}, rotate_field=None)
    inst = (data.get("instance_url") or "").rstrip("/")
    if not inst:
        raise RuntimeError("Salesforce sign-in did not return an instance URL.")
    return inst, {"Authorization": f"Bearer {data['access_token']}"}


def soql_query(ctx, args, api):
    inst, headers = _session(api)
    return api.http(ctx, "GET", f"{inst}/services/data/{VERSION}/query",
                    headers=headers, params={"q": args["soql"]})


def record_get(ctx, args, api):
    inst, headers = _session(api)
    return api.http(ctx, "GET", f"{inst}/services/data/{VERSION}/sobjects/{args['sobject']}/{args['id']}",
                    headers=headers)


def record_create(ctx, args, api):
    inst, headers = _session(api)
    fields = dict(args.get("fields") or {})
    if not fields:
        raise RuntimeError("Give at least one field, e.g. {\"fields\": {\"LastName\": \"Nkomo\", \"Company\": \"Acme\"}}.")
    return api.http(ctx, "POST", f"{inst}/services/data/{VERSION}/sobjects/{args['sobject']}",
                    headers=headers, json_body=fields)


def record_update(ctx, args, api):
    inst, headers = _session(api)
    fields = dict(args.get("fields") or {})
    if not fields:
        raise RuntimeError("Give at least one field to change.")
    return api.http(ctx, "PATCH", f"{inst}/services/data/{VERSION}/sobjects/{args['sobject']}/{args['id']}",
                    headers=headers, json_body=fields)


def register(api):
    return [
        {"name": "query", "description": "Run a SOQL query, e.g. SELECT Name, Email FROM Contact WHERE Email != null LIMIT 20. Returns records.",
         "input_schema": {"type": "object", "properties": {"soql": {"type": "string", "description": "SOQL query"}}, "required": ["soql"]},
         "handler": lambda ctx, a: soql_query(ctx, a, api)},
        {"name": "record", "description": "Read one record by object and id (Lead, Contact, Account, Opportunity, ...).",
         "input_schema": {"type": "object", "properties": {
             "sobject": {"type": "string", "description": "Object name, e.g. Lead"},
             "id": {"type": "string", "description": "Record id"}}, "required": ["sobject", "id"]},
         "handler": lambda ctx, a: record_get(ctx, a, api)},
        {"name": "create", "description": "Create a record. Fields is a name->value map, e.g. {\"LastName\": \"Nkomo\", \"Company\": \"Acme\"} for a Lead.",
         "input_schema": {"type": "object", "properties": {
             "sobject": {"type": "string", "description": "Object name"},
             "fields": {"type": "object", "description": "Field values"}}, "required": ["sobject", "fields"]},
         "handler": lambda ctx, a: record_create(ctx, a, api), "risk": "write"},
        {"name": "update", "description": "Update a record's fields.",
         "input_schema": {"type": "object", "properties": {
             "sobject": {"type": "string", "description": "Object name"},
             "id": {"type": "string", "description": "Record id"},
             "fields": {"type": "object", "description": "Changed fields"}}, "required": ["sobject", "id", "fields"]},
         "handler": lambda ctx, a: record_update(ctx, a, api), "risk": "write"},
    ]
