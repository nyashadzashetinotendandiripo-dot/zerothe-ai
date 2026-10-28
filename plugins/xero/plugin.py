"""Xero plugin: contacts, invoices and accounts via OAuth2 (refresh rotation handled).

First token comes from Xero's OAuth flow once; every refresh hands back a new refresh
token which is saved back to the credential store automatically.
"""
from __future__ import annotations

import base64

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

TOKEN_URL = "https://identity.xero.com/connect/token"
API = "https://api.xero.com/api.xro/2.0"


def _token(api) -> str:
    cid, csec, rt = api.config("client_id"), api.config("client_secret"), api.config("refresh_token")
    if not (cid and csec and rt):
        raise RuntimeError("Xero is not configured: paste the Client ID, Client secret and Refresh token in Plugins.")
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    data = oauth2_token(None, "xero", TOKEN_URL,
                        {"grant_type": "refresh_token", "refresh_token": rt},
                        headers={"Authorization": f"Basic {basic}"})
    return data["access_token"]


def _headers(api, tenant_required=True) -> dict:
    h = {"Authorization": f"Bearer {_token(api)}", "Accept": "application/json"}
    if tenant_required:
        tenant = (api.config("tenant_id") or "").strip()
        if not tenant:
            raise RuntimeError("Xero needs a Tenant ID: run the tenants tool once any token works and paste it in Plugins.")
        h["Xero-Tenant-Id"] = tenant
    return h


def tenants_list(ctx, args, api):
    tok = _token(api)
    return api.http(ctx, "GET", "https://api.xero.com/connections",
                    headers={"Authorization": f"Bearer {tok}"})


def contacts(ctx, args, api):
    params = {}
    if args.get("where"):
        params["where"] = args["where"]
    params["page"] = int(args.get("page", 1) or 1)
    return api.http(ctx, "GET", f"{API}/Contacts", headers=_headers(api), params=params)


def contact_create(ctx, args, api):
    body = {"Name": args["name"]}
    if args.get("email"):
        body["EmailAddress"] = args["email"]
    if args.get("phone"):
        body["Phones"] = [{"PhoneType": "MOBILE", "PhoneNumber": args["phone"]}]
    return api.http(ctx, "POST", f"{API}/Contacts", headers=_headers(api), json_body={"Contacts": [body]})


def invoices(ctx, args, api):
    params = {}
    if args.get("where"):
        params["where"] = args["where"]
    if args.get("status"):
        params["Statuses"] = args["status"]
    params["page"] = int(args.get("page", 1) or 1)
    params["order"] = "Date DESC"
    return api.http(ctx, "GET", f"{API}/Invoices", headers=_headers(api), params=params)


def invoice_create(ctx, args, api):
    lines = []
    for ln in args.get("lines") or []:
        item = {"Description": ln.get("description", ""), "Quantity": float(ln.get("qty", 1) or 1),
                "UnitAmount": float(ln.get("unit_amount", 0) or 0)}
        if ln.get("account_code"):
            item["AccountCode"] = ln["account_code"]
        if ln.get("tax_type"):
            item["TaxType"] = ln["tax_type"]
        lines.append(item)
    if not lines:
        raise RuntimeError("Give at least one line: description, qty, unit_amount.")
    body = {"Type": args.get("type", "ACCREC"), "Contact": {"ContactID": args["contact_id"]}, "LineItems": lines}
    for k in ("date", "due_date", "reference"):
        if args.get(k):
            body[{"date": "Date", "due_date": "DueDate", "reference": "Reference"}[k]] = args[k]
    return api.http(ctx, "POST", f"{API}/Invoices", headers=_headers(api), json_body={"Invoices": [body]})


def accounts(ctx, args, api):
    params = {}
    if args.get("where"):
        params["where"] = args["where"]
    return api.http(ctx, "GET", f"{API}/Accounts", headers=_headers(api), params=params)


def register(api):
    return [
        {"name": "tenants", "description": "List Xero organisations (tenant id + name) this sign-in can see. Run first to get the Tenant ID.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: tenants_list(ctx, a, api)},
        {"name": "contacts", "description": "List contacts, newest first. Optional Xero where-filter, e.g. EmailAddress.StartsWith(\"a\").",
         "input_schema": {"type": "object", "properties": {"where": {"type": "string"}, "page": {"type": "integer"}}},
         "handler": lambda ctx, a: contacts(ctx, a, api)},
        {"name": "contact_create", "description": "Create a contact (name required, email/phone optional).",
         "input_schema": {"type": "object", "properties": {
             "name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}}, "required": ["name"]},
         "handler": lambda ctx, a: contact_create(ctx, a, api), "risk": "write"},
        {"name": "invoices", "description": "List invoices, newest first. Optional where-filter and status (e.g. AUTHORISED).",
         "input_schema": {"type": "object", "properties": {"where": {"type": "string"}, "status": {"type": "string"}, "page": {"type": "integer"}}},
         "handler": lambda ctx, a: invoices(ctx, a, api)},
        {"name": "invoice_create", "description": "Create a sales invoice (ACCREC) or bill (ACCPAY). Look up the contact id with contacts and account codes with accounts first.",
         "input_schema": {"type": "object", "properties": {
             "contact_id": {"type": "string"}, "type": {"type": "string", "description": "ACCREC (default) or ACCPAY"},
             "lines": {"type": "array", "description": "Items with description, qty, unit_amount, account_code, tax_type",
                       "items": {"type": "object"}},
             "date": {"type": "string"}, "due_date": {"type": "string"}, "reference": {"type": "string"}},
             "required": ["contact_id", "lines"]},
         "handler": lambda ctx, a: invoice_create(ctx, a, api), "risk": "write"},
        {"name": "accounts", "description": "Chart of accounts (codes and names) for invoice line items.",
         "input_schema": {"type": "object", "properties": {"where": {"type": "string"}}},
         "handler": lambda ctx, a: accounts(ctx, a, api)},
    ]
