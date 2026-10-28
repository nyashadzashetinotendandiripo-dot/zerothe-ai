"""QuickBooks Online plugin: customers, invoices and free-form queries.

Auth is OAuth2 with a refresh token (paste once from Intuit's OAuth playground).
Access tokens are refreshed automatically; rotated refresh tokens are saved back.
Set sandbox=yes while testing so no real books change.
"""
from __future__ import annotations

import base64

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


def _auth(api) -> tuple[str, str]:
    cid, csec = api.config("client_id"), api.config("client_secret")
    rt, realm = api.config("refresh_token"), (api.config("realm_id") or "").strip()
    if not (cid and csec and rt and realm):
        raise RuntimeError("QuickBooks is not configured: paste the Client ID, Client secret, Refresh token and Company ID in Plugins.")
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    data = oauth2_token(None, "quickbooks", TOKEN_URL,
                        {"grant_type": "refresh_token", "refresh_token": rt},
                        headers={"Authorization": f"Basic {basic}", "Accept": "application/json"})
    host = "sandbox-quickbooks.api.intuit.com" if (api.config("sandbox", "no") or "").strip().lower() in ("yes", "true", "1") else "quickbooks.api.intuit.com"
    base = f"https://{host}/v3/company/{realm}"
    return base, {"Authorization": f"Bearer {data['access_token']}", "Accept": "application/json"}


def company(ctx, args, api):
    base, headers = _auth(api)
    realm = (api.config("realm_id") or "").strip()
    return api.http(ctx, "GET", f"{base}/companyinfo/{realm}", headers=headers)


def query(ctx, args, api):
    base, headers = _auth(api)
    return api.http(ctx, "POST", f"{base}/query?minorversion=75", headers={**headers, "Content-Type": "application/text"},
                    data=args["sql"])


def customers(ctx, args, api):
    base, headers = _auth(api)
    q = "select * from Customer maxresults 30"
    if args.get("name"):
        safe = str(args["name"]).replace("'", "\\'")
        q = f"select * from Customer where DisplayName like '%{safe}%' maxresults 30"
    return api.http(ctx, "POST", f"{base}/query?minorversion=75", headers={**headers, "Content-Type": "application/text"}, data=q)


def invoice_create(ctx, args, api):
    base, headers = _auth(api)
    line = {"Amount": float(args["amount"]), "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {"ItemRef": {"value": str(args["item_id"])},
                                    "Qty": float(args.get("qty", 1) or 1),
                                    "UnitPrice": float(args["amount"]) / float(args.get("qty", 1) or 1)}}
    if args.get("description"):
        line["Description"] = args["description"]
    body = {"CustomerRef": {"value": str(args["customer_id"])}, "Line": [line]}
    if args.get("due_date"):
        body["DueDate"] = args["due_date"]
    if args.get("doc_number"):
        body["DocNumber"] = args["doc_number"]
    return api.http(ctx, "POST", f"{base}/invoice?minorversion=75", headers=headers, json_body=body)


def register(api):
    return [
        {"name": "company", "description": "Which QuickBooks company is connected (name, address). Proves the sign-in works.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: company(ctx, a, api)},
        {"name": "query", "description": "Run any QuickBooks query, e.g. select * from Invoice where Balance > '0' maxresults 20. Tables: Customer, Invoice, Item, Account, Payment, Bill.",
         "input_schema": {"type": "object", "properties": {"sql": {"type": "string", "description": "Query text"}}, "required": ["sql"]},
         "handler": lambda ctx, a: query(ctx, a, api)},
        {"name": "customers", "description": "List customers, optionally filtered by name.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Name to search for"}}},
         "handler": lambda ctx, a: customers(ctx, a, api)},
        {"name": "invoice_create", "description": "Create a sales invoice. Find the customer id via customers and the item id via query (select * from Item).",
         "input_schema": {"type": "object", "properties": {
             "customer_id": {"type": "string"}, "item_id": {"type": "string", "description": "Sales item id from the Item list"},
             "amount": {"type": "number", "description": "Line total"},
             "qty": {"type": "number", "description": "Quantity, default 1"},
             "description": {"type": "string"}, "due_date": {"type": "string", "description": "YYYY-MM-DD"},
             "doc_number": {"type": "string"}}, "required": ["customer_id", "item_id", "amount"]},
         "handler": lambda ctx, a: invoice_create(ctx, a, api), "risk": "write"},
    ]
