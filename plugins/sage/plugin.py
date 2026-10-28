"""Sage Business Cloud Accounting plugin: contacts, sales invoices, ledgers, tax rates.

First token comes from Sage's OAuth flow once; refreshes (and rotated refresh tokens)
are handled automatically.
"""
from __future__ import annotations

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

TOKEN_URL = "https://oauth.accounting.sage.com/token"
API = "https://api.accounting.sage.com/v3.1"


def _token_url(api) -> str:
    # Sage hosts token endpoints per region (UK default; US: https://oauth.na.sageone.com/token).
    return ((api.config("token_url") or "").strip() or TOKEN_URL).rstrip("/")


def _api(api) -> str:
    return ((api.config("api_base") or "").strip() or API).rstrip("/")


def _auth(api) -> dict:
    cid, csec, rt = api.config("client_id"), api.config("client_secret"), api.config("refresh_token")
    if not (cid and csec and rt):
        raise RuntimeError("Sage is not configured: paste the Client ID + secret, then press Connect (or paste a Refresh token).")
    data = oauth2_token(None, "sage", _token_url(api),
                        {"grant_type": "refresh_token", "client_id": cid, "client_secret": csec, "refresh_token": rt})
    return {"Authorization": f"Bearer {data['access_token']}"}


def contacts(ctx, args, api):
    params = {"items_per_page": 50}
    if args.get("search"):
        params["search"] = args["search"]
    return api.http(ctx, "GET", f"{_api(api)}/contacts", headers=_auth(api), params=params)


def contact_create(ctx, args, api):
    body = {"name": args["name"], "contact_type_ids": ["CUSTOMER"]}
    if args.get("email"):
        body["email"] = args["email"]
    if args.get("phone"):
        body["phone"] = args["phone"]
    return api.http(ctx, "POST", f"{_api(api)}/contacts", headers=_auth(api), json_body=body)


def sales_invoices(ctx, args, api):
    params = {"items_per_page": 50}
    if args.get("search"):
        params["search"] = args["search"]
    return api.http(ctx, "GET", f"{_api(api)}/sales_invoices", headers=_auth(api), params=params)


def sales_invoice_create(ctx, args, api):
    lines = []
    for ln in args.get("lines") or []:
        item = {"description": ln.get("description", ""), "quantity": float(ln.get("qty", 1) or 1),
                "unit_price": float(ln.get("unit_price", 0) or 0)}
        if ln.get("ledger_account_id"):
            item["ledger_account_id"] = ln["ledger_account_id"]
        if ln.get("tax_rate_id"):
            item["tax_rate_id"] = ln["tax_rate_id"]
        lines.append(item)
    if not lines:
        raise RuntimeError("Give at least one line: description, qty, unit_price.")
    body = {"contact_id": args["contact_id"], "date": args["date"], "invoice_lines": lines}
    if args.get("due_date"):
        body["due_date"] = args["due_date"]
    return api.http(ctx, "POST", f"{_api(api)}/sales_invoices", headers=_auth(api), json_body=body)


def ledger_accounts(ctx, args, api):
    return api.http(ctx, "GET", f"{_api(api)}/ledger_accounts", headers=_auth(api), params={"items_per_page": 100})


def tax_rates(ctx, args, api):
    return api.http(ctx, "GET", f"{_api(api)}/tax_rates", headers=_auth(api), params={"items_per_page": 100})


def register(api):
    return [
        {"name": "contacts", "description": "Search Sage contacts (customers and suppliers). Empty search lists them.",
         "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
         "handler": lambda ctx, a: contacts(ctx, a, api)},
        {"name": "contact_create", "description": "Create a customer contact (name required, email/phone optional).",
         "input_schema": {"type": "object", "properties": {
             "name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}}, "required": ["name"]},
         "handler": lambda ctx, a: contact_create(ctx, a, api), "risk": "write"},
        {"name": "sales_invoices", "description": "List sales invoices, newest first. Optional search text.",
         "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
         "handler": lambda ctx, a: sales_invoices(ctx, a, api)},
        {"name": "sales_invoice_create", "description": "Create a sales invoice. Look up ledger_account_id and tax_rate_id with the ledger_accounts and tax_rates tools first.",
         "input_schema": {"type": "object", "properties": {
             "contact_id": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"},
             "due_date": {"type": "string", "description": "YYYY-MM-DD"},
             "lines": {"type": "array", "description": "Items with description, qty, unit_price, ledger_account_id, tax_rate_id",
                       "items": {"type": "object"}}}, "required": ["contact_id", "date", "lines"]},
         "handler": lambda ctx, a: sales_invoice_create(ctx, a, api), "risk": "write"},
        {"name": "ledger_accounts", "description": "Chart of ledger accounts (ids and names) for invoice lines.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: ledger_accounts(ctx, a, api)},
        {"name": "tax_rates", "description": "Tax rates (ids, names, percentages) for invoice lines.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: tax_rates(ctx, a, api)},
    ]
