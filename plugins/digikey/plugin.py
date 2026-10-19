"""DigiKey product sourcing: live component search, stock and unit pricing.

Uses the DigiKey API v4 (OAuth2 client-credentials grant). Credentials come from
the plugin's secret fields (stored in the OS keyring); nothing is ordered here -
search tools are read-only and safe.
"""
import time


def register(api):
    state = {"token": "", "exp": 0.0}

    def _token(ctx):
        now = time.time()
        if state["token"] and now < state["exp"] - 60:
            return state["token"]
        data = api.http(ctx, "POST", "https://api.digikey.com/v1/oauth2/token",
                        data={"grant_type": "client_credentials", "client_id": api.config("client_id"),
                              "client_secret": api.config("client_secret")})
        if not isinstance(data, dict) or not data.get("access_token"):
            raise RuntimeError("DigiKey did not return an access token. Check the client id/secret in Plugins.")
        state["token"] = data["access_token"]
        state["exp"] = now + float(data.get("expires_in") or 60)
        return state["token"]

    def _headers(ctx):
        return {"Authorization": "Bearer " + _token(ctx), "X-DIGIKEY-Client-Id": api.config("client_id")}

    def _compact(products):
        out = []
        for p in (products or [])[:50]:
            if not isinstance(p, dict):
                continue
            man = p.get("Manufacturer") or {}
            desc = p.get("Description") or {}
            out.append({
                "part_number": p.get("ManufacturerPartNumber", ""),
                "manufacturer": (man.get("Name", "") if isinstance(man, dict) else str(man)),
                "description": (desc.get("ProductDescription", "") if isinstance(desc, dict) else str(desc))[:220],
                "stock": p.get("QuantityAvailable", 0),
                "unit_price": p.get("UnitPrice"),
                "status": p.get("ProductStatus", ""),
                "url": p.get("ProductUrl", ""),
                "photo": p.get("PhotoUrl", ""),
                "datasheet": p.get("DataSheetUrl", ""),
            })
        return out

    def _result(r):
        if not isinstance(r, dict):
            return r
        return {"total": r.get("TotalResults", len(_compact(r.get("Products")))),
                "products": _compact(r.get("Products"))}

    def search_keyword(ctx, args):
        kw = str(args.get("keyword") or "").strip()
        if not kw:
            raise RuntimeError("Give a keyword, like 'LM7805' or '100uF 50V capacitor'.")
        n = max(1, min(int(args.get("limit") or 25), 50))
        r = api.http(ctx, "POST", "https://api.digikey.com/products/v4/search/keyword",
                     headers=_headers(ctx), json_body={"Keywords": kw, "Limit": n})
        return _result(r)

    def search_part_number(ctx, args):
        pn = str(args.get("part_number") or "").strip()
        if not pn:
            raise RuntimeError("Give a manufacturer part number, like 'LM7805'.")
        n = max(1, min(int(args.get("limit") or 25), 50))
        r = api.http(ctx, "POST", "https://api.digikey.com/products/v4/search/partnumber",
                     headers=_headers(ctx), json_body={"pns": [{"PartNumber": pn}], "limit": n})
        return _result(r)

    return [
        {"name": "search_keyword",
         "description": "Search DigiKey for electronic components by keyword or part number phrase. "
                        "Returns manufacturer part numbers, stock, unit pricing, datasheet and product URLs.",
         "input_schema": {"type": "object",
                          "properties": {"keyword": {"type": "string", "description": "e.g. LM7805, 100uF 50V, STM32F103"},
                                         "limit": {"type": "integer", "description": "results limit, default 25, max 50"}},
                          "required": ["keyword"]},
         "handler": search_keyword},
        {"name": "search_part_number",
         "description": "Exact/partial manufacturer-part-number lookup on DigiKey (stock and pricing for a specific part).",
         "input_schema": {"type": "object",
                          "properties": {"part_number": {"type": "string", "description": "e.g. LM7805"},
                                         "limit": {"type": "integer", "description": "results limit, default 25, max 50"}},
                          "required": ["part_number"]},
         "handler": search_part_number},
    ]
