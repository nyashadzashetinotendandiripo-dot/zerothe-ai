"""New app plugins: manifest validity, declarative wiring, Chrome local data,
OAuth2 helper, Zoom + Salesforce handlers.  python -m unittest tests.test_newplugins -v"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-newplug-"))

from core.plugins import build_declarative, build_python  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS = ["asana", "hubspot", "x", "chrome-local", "zoom", "salesforce",
       "quickbooks", "xero", "sage", "teams", "onshape", "fusion"]


def load_manifest(pid):
    return json.loads((ROOT / "plugins" / pid / "plugin.json").read_text(encoding="utf-8"))


def load_py(pid):
    spec = importlib.util.spec_from_file_location(f"plug_{pid}", ROOT / "plugins" / pid / "plugin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeApi:
    def __init__(self, values=None):
        self.values = values or {}
        self.calls = []

    def config(self, key, default=""):
        return self.values.get(key, default)

    def http(self, ctx, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self.calls[-1].get("_resp", {"ok": True})


class ManifestTests(unittest.TestCase):
    KNOWN_RISK = {"safe", "send", "write", "delete", "submit", "purchase", "command"}

    def test_manifests_valid_and_unique(self):
        seen = set()
        for pid in IDS:
            m = load_manifest(pid)
            self.assertEqual(m["id"], pid)
            for k in ("name", "description", "help"):
                self.assertTrue(m.get(k), f"{pid}.{k}")
            self.assertNotIn(pid, seen)
            seen.add(pid)
            secret_fields = {f["key"] for f in m.get("fields", []) if f.get("secret")}
            for t in m.get("tools", []):
                for k in ("name", "description", "input_schema"):
                    self.assertTrue(t.get(k), f"{pid}.{t.get('name')}.{k}")
                self.assertIn(t.get("risk", "safe"), self.KNOWN_RISK)
                blob = json.dumps(t)
                for ref in set(__import__("re").findall(r"\{\{\s*secret\.(\w+)\s*\}\}", blob)):
                    self.assertIn(ref, secret_fields, f"{pid} uses undeclared secret {ref}")

    def test_declarative_tools_build(self):
        expect = {"asana": {"asana_me", "asana_my_tasks", "asana_create_task"},
                  "hubspot": {"hubspot_find_contact", "hubspot_create_contact", "hubspot_list_deals"}}
        for pid, want in expect.items():
            m = load_manifest(pid)
            specs = build_declarative({**m, "id": pid})(None)
            self.assertTrue(want <= {s.name for s in specs}, pid)
        xspecs = build_python(ROOT / "plugins" / "x", {"id": "x", "name": "X"})(None)
        self.assertTrue({"x_me", "x_search", "x_post"} <= {s.name for s in xspecs})


class DeclarativeWireTests(unittest.TestCase):
    def _run(self, pid, tool, args, resp):
        m = load_manifest(pid)
        conn = SimpleNamespace(cfg=lambda k, d="": {"token": "sekrit"}.get(k, d))

        def fake_http(ctx, method, url, **kw):
            self.captured = {"method": method, "url": url, **kw}
            return resp
        conn.http = fake_http
        specs = build_declarative({**m, "id": pid})(conn)
        spec = next(s for s in specs if s.name == f"{pid}_{tool}")
        return spec.handler(None, args)

    def test_asana_auth_and_url(self):
        out = self._run("asana", "my_tasks", {"workspace": "w1"}, {"data": []})
        self.assertEqual(self.captured["headers"]["Authorization"], "Bearer sekrit")
        self.assertIn("/tasks", self.captured["url"])
        self.assertEqual(self.captured["params"]["workspace"], "w1")

    def test_hubspot_search_body(self):
        out = self._run("hubspot", "find_contact", {"email": "a@b.co"}, {"results": [{"id": "1"}]})
        body = self.captured["json_body"]
        self.assertEqual(body["filterGroups"][0]["filters"][0]["value"], "a@b.co")
        self.assertEqual(out.text[:1], "[")  # response_path results -> list passthrough

    def test_x_post_is_gated(self):
        xspecs = build_python(ROOT / "plugins" / "x", {"id": "x", "name": "X"})(None)
        by = {s.name: s for s in xspecs}
        self.assertIsNotNone(by["x_post"].risk)
        self.assertTrue(by["x_me"].read_only)


class ChromeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="zttest-chrome-"))
        prof = self.root / "Default"
        prof.mkdir()
        (prof / "Bookmarks").write_text(json.dumps({"roots": {"bookmark_bar": {"children": [
            {"type": "url", "name": "Joburg Jobs", "url": "https://jobs.example/"},
            {"type": "folder", "name": "News", "children": [
                {"type": "url", "name": "Tech News", "url": "https://news.example/tech"}]}]}}}), encoding="utf-8")
        con = sqlite3.connect(prof / "History")
        con.execute("CREATE TABLE urls(url TEXT, title TEXT, visit_count INT, last_visit_time INT)")
        con.execute("INSERT INTO urls VALUES(?,?,?,?)", ("https://jobs.example/apply", "Apply", 5, 13400000000000000))
        con.execute("INSERT INTO urls VALUES(?,?,?,?)", ("https://other.example/", "Other", 1, 13400000000000000))
        con.commit()
        con.close()
        self.mod = load_py("chrome-local")
        import unittest.mock as mock
        self._patch = mock.patch.object(self.mod, "_user_data", lambda: self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def api(self):
        return FakeApi({})

    def test_bookmarks_search(self):
        out = self.mod.chrome_bookmarks({"query": "jobs"}, self.api())
        self.assertEqual(out["bookmarks"][0]["url"], "https://jobs.example/")
        self.assertEqual(out["bookmarks"][0]["folder"], "")

    def test_bookmarks_nested_folder(self):
        out = self.mod.chrome_bookmarks({"query": "tech"}, self.api())
        self.assertEqual(out["bookmarks"][0]["folder"], "News")

    def test_history_search_and_time(self):
        out = self.mod.chrome_history({"query": "apply", "limit": 5}, self.api())
        self.assertEqual(len(out["history"]), 1)
        self.assertTrue(out["history"][0]["last_visit"].startswith("2025-"), out["history"][0])

    def test_profiles(self):
        out = self.mod.chrome_profiles({}, self.api())
        self.assertIn("Default", out["profiles"])

    def test_missing_profile(self):
        api = FakeApi({"profile": "Ghost"})
        out = self.mod.chrome_bookmarks({"query": ""}, api)
        self.assertIn("not found", out)


class OAuthHelperTests(unittest.TestCase):
    def setUp(self):
        import core.plugins as plugmod
        self.hits = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n).decode()
                outer.hits.append(body)
                if "bad" in body:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "invalid_grant"}')
                    return
                payload = {"access_token": "tok123", "expires_in": 3600}
                if "grant_type=refresh_token" in body:
                    payload["refresh_token"] = "new-refresh"
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/token"
        plugmod._OAUTH2_CACHE.pop("tplug", None)
        self.addCleanup(self.srv.shutdown)

    def test_token_cached_and_rotated(self):
        import core.plugins as plugmod
        import unittest.mock as mock
        saved = {}

        class Cfg:
            def cfg(self, k, d=""):
                return {"client_id": "c", "client_secret": "s", "refresh_token": "old"} .get(k, d)
        with mock.patch.object(plugmod.secrets, "set_secret", lambda n, v: saved.__setitem__(n, v)):
            d1 = plugmod.oauth2_token(Cfg(), "tplug", self.url, {"grant_type": "refresh_token", "refresh_token": "old"})
            d2 = plugmod.oauth2_token(Cfg(), "tplug", self.url, {"grant_type": "refresh_token", "refresh_token": "old"})
        self.assertEqual(d1["access_token"], "tok123")
        self.assertEqual(len(self.hits), 1, "second call must come from cache")
        self.assertEqual(saved.get("plugin:tplug:refresh_token"), "new-refresh")

    def test_bad_grant_raises(self):
        import core.plugins as plugmod
        from core.plugins import ConnectorError

        class Cfg:
            def cfg(self, k, d=""):
                return ""
        with self.assertRaises(ConnectorError):
            plugmod.oauth2_token(Cfg(), "tplug", self.url, {"grant_type": "bad"})


class ZoomSalesforceTests(unittest.TestCase):
    def test_register_shapes(self):
        z = load_py("zoom")
        s = load_py("salesforce")
        zt = {t["name"]: t for t in z.register(FakeApi())}
        st = {t["name"]: t for t in s.register(FakeApi())}
        self.assertEqual(set(zt), {"meetings", "meeting", "create_meeting", "recordings"})
        self.assertEqual(set(st), {"query", "record", "create", "update"})
        self.assertEqual(zt["create_meeting"].get("risk"), "write")
        self.assertEqual(st["create"].get("risk"), "write")
        self.assertNotIn("risk", zt["meetings"])

    def test_zoom_missing_config(self):
        z = load_py("zoom")
        tools = {t["name"]: t for t in z.register(FakeApi())}
        with self.assertRaises(RuntimeError):
            tools["meetings"]["handler"](None, {})

    def test_zoom_meeting_create_body(self):
        z = load_py("zoom")
        z.oauth2_token = lambda *a, **k: {"access_token": "t"}
        api = FakeApi({"client_id": "c", "client_secret": "s", "account_id": "a"})
        tools = {t["name"]: t for t in z.register(api)}
        tools["create_meeting"]["handler"](None, {"topic": "Standup", "start_time": "2026-10-20T09:00:00"})
        call = api.calls[-1]
        self.assertIn("/users/me/meetings", call["url"])
        self.assertEqual(call["json_body"]["topic"], "Standup")

    def test_salesforce_query_url(self):
        s = load_py("salesforce")
        s.oauth2_token = lambda *a, **k: {"access_token": "t", "instance_url": "https://na1.salesforce.com"}
        api = FakeApi({"username": "u", "password": "p", "client_id": "c", "client_secret": "s"})
        tools = {t["name"]: t for t in s.register(api)}
        tools["query"]["handler"](None, {"soql": "SELECT Id FROM Lead LIMIT 1"})
        call = api.calls[-1]
        self.assertIn("/services/data/v60.0/query", call["url"])
        self.assertIn("SELECT", call["params"]["q"])

    def test_salesforce_refresh_sends_redirect_uri(self):
        s = load_py("salesforce")
        captured = {}

        def fake_token(c, pid, url, payload, headers=None, rotate_field="refresh_token"):
            captured.update(payload=dict(payload), url=url)
            return {"access_token": "t", "instance_url": "https://na1.salesforce.com"}

        s.oauth2_token = fake_token
        import core.plugins as plugmod
        import unittest.mock as mock
        api = FakeApi({"client_id": "c", "client_secret": "s"})
        sec = {"plugin:salesforce:refresh_token": "rt",
               "plugin:salesforce:redirect_uri": "http://127.0.0.1:8765/oauth/callback"}
        with mock.patch.object(plugmod.secrets, "get_secret", lambda n: sec.get(n)):
            inst, headers = s._session(api)
        self.assertTrue(inst.startswith("https://"))
        self.assertEqual(headers, {"Authorization": "Bearer t"})
        self.assertEqual(captured["payload"].get("redirect_uri"), "http://127.0.0.1:8765/oauth/callback")
        self.assertIn("login.salesforce.com", captured["url"])

    def test_x_public_client_refresh_includes_client_id(self):
        x = load_py("x")
        captured = {}

        def fake_token(c, pid, url, payload, headers=None, rotate_field="refresh_token"):
            captured.update(payload=dict(payload), headers=headers)
            return {"access_token": "t"}

        x.oauth2_token = fake_token
        import core.plugins as plugmod
        import unittest.mock as mock
        api = FakeApi({"client_id": "cid-public"})
        with mock.patch.object(plugmod.secrets, "get_secret", lambda n: {"plugin:x:refresh_token": "rt"}.get(n)):
            self.assertEqual(x._auth(api), {"Authorization": "Bearer t"})
        self.assertEqual(captured["payload"].get("client_id"), "cid-public")
        self.assertIsNone(captured["headers"])

    def test_build_python_specs(self):
        specs = build_python(ROOT / "plugins" / "zoom", {"id": "zoom", "name": "Zoom"})(None)
        by = {sp.name: sp for sp in specs}
        self.assertIn("zoom_create_meeting", by)
        self.assertIsNotNone(by["zoom_create_meeting"].risk)


class AcctgTeamsTests(unittest.TestCase):
    def api(self, values):
        return FakeApi(values)

    def test_quickbooks_query_and_sandbox(self):
        qb = load_py("quickbooks")
        qb.oauth2_token = lambda *a, **k: {"access_token": "t"}
        api = self.api({"client_id": "c", "client_secret": "s", "refresh_token": "r",
                        "realm_id": "123", "sandbox": "yes"})
        tools = {t["name"]: t for t in qb.register(api)}
        self.assertEqual(set(tools), {"company", "query", "customers", "invoice_create"})
        tools["query"]["handler"](None, {"sql": "select * from Invoice maxresults 5"})
        call = api.calls[-1]
        self.assertIn("sandbox-quickbooks.api.intuit.com/v3/company/123/query", call["url"])
        self.assertIn("application/text", call["headers"].get("Content-Type", ""))
        self.assertEqual(call["data"], "select * from Invoice maxresults 5")
        tools["invoice_create"]["handler"](None, {"customer_id": "9", "item_id": "1", "amount": 100})
        body = api.calls[-1]["json_body"]
        self.assertEqual(body["CustomerRef"], {"value": "9"})
        self.assertEqual(body["Line"][0]["SalesItemLineDetail"]["ItemRef"], {"value": "1"})
        prod = self.api({"client_id": "c", "client_secret": "s", "refresh_token": "r", "realm_id": "123"})
        qb.register(prod)  # smoke
        tools2 = {t["name"]: t for t in qb.register(self.api({}))}
        with self.assertRaises(RuntimeError):
            tools2["query"]["handler"](None, {"sql": "select 1"})

    def test_quickbooks_prod_host(self):
        qb = load_py("quickbooks")
        qb.oauth2_token = lambda *a, **k: {"access_token": "t"}
        api = self.api({"client_id": "c", "client_secret": "s", "refresh_token": "r", "realm_id": "123"})
        tools = {t["name"]: t for t in qb.register(api)}
        tools["company"]["handler"](None, {})
        self.assertIn("quickbooks.api.intuit.com/v3/company/123/companyinfo/123", api.calls[-1]["url"])

    def test_xero_tenant_and_wrapping(self):
        xe = load_py("xero")
        xe.oauth2_token = lambda *a, **k: {"access_token": "t"}
        api = self.api({"client_id": "c", "client_secret": "s", "refresh_token": "r", "tenant_id": "ten1"})
        tools = {t["name"]: t for t in xe.register(api)}
        self.assertEqual(set(tools), {"tenants", "contacts", "contact_create", "invoices", "invoice_create", "accounts"})
        tools["contact_create"]["handler"](None, {"name": "Nkomo"})
        call = api.calls[-1]
        self.assertEqual(call["json_body"], {"Contacts": [{"Name": "Nkomo"}]})
        self.assertEqual(call["headers"].get("Xero-Tenant-Id"), "ten1")
        tools["invoice_create"]["handler"](None, {"contact_id": "c1", "lines": [{"description": "Work", "qty": 2, "unit_amount": 50}]})
        inv = api.calls[-1]["json_body"]["Invoices"][0]
        self.assertEqual(inv["Contact"], {"ContactID": "c1"})
        self.assertEqual(inv["LineItems"][0]["UnitAmount"], 50)
        no_tenant = self.api({"client_id": "c", "client_secret": "s", "refresh_token": "r"})
        tools_nt = {t["name"]: t for t in xe.register(no_tenant)}
        with self.assertRaises(RuntimeError):
            tools_nt["contacts"]["handler"](None, {})
        tenants = tools_nt["tenants"]["handler"](None, {})
        self.assertIn("connections", no_tenant.calls[-1]["url"])
        self.assertNotIn("Xero-Tenant-Id", no_tenant.calls[-1].get("headers", {}))

    def test_sage_bodies(self):
        sg = load_py("sage")
        sg.oauth2_token = lambda *a, **k: {"access_token": "t"}
        api = self.api({"client_id": "c", "client_secret": "s", "refresh_token": "r"})
        tools = {t["name"]: t for t in sg.register(api)}
        self.assertEqual(set(tools), {"contacts", "contact_create", "sales_invoices", "sales_invoice_create",
                                      "ledger_accounts", "tax_rates"})
        tools["contact_create"]["handler"](None, {"name": "Acme"})
        self.assertEqual(api.calls[-1]["json_body"]["contact_type_ids"], ["CUSTOMER"])
        tools["sales_invoice_create"]["handler"](
            None, {"contact_id": "cc1", "date": "2026-10-20",
                   "lines": [{"description": "Work", "qty": 1, "unit_price": 200, "ledger_account_id": "L1"}]})
        body = api.calls[-1]["json_body"]
        self.assertEqual(body["invoice_lines"][0]["ledger_account_id"], "L1")
        self.assertIn("/sales_invoices", api.calls[-1]["url"])

    def test_teams_device_flow(self):
        tm = load_py("teams")
        api = self.api({"client_id": "cid123"})
        tm._form = lambda url, data: (200, {"user_code": "AB12-CD34", "device_code": "dev1",
                                            "verification_uri": "https://microsoft.com/devicelogin",
                                            "expires_in": 900, "interval": 0}) if "devicecode" in url else (200, {
                                                "access_token": "tok", "refresh_token": "rt1", "expires_in": 3600})
        saved = {}
        tm._secrets = SimpleNamespace(set_secret=lambda n, v: saved.__setitem__(n, v))
        tools = {t["name"]: t for t in tm.register(api)}
        ctx = SimpleNamespace(bot={"id": "b1"})
        start = tools["connect_start"]["handler"](ctx, {})
        self.assertIn("AB12-CD34", start)
        done = tools["connect_finish"]["handler"](ctx, {})
        self.assertIn("paired", done)
        self.assertEqual(saved.get("plugin:teams:refresh_token"), "rt1")

    def test_teams_refresh_includes_secret_when_configured(self):
        tm = load_py("teams")
        captured = {}

        def fake_token(c, pid, url, payload, headers=None, rotate_field="refresh_token"):
            captured.update(payload=dict(payload))
            return {"access_token": "t"}

        tm.oauth2_token = fake_token
        api = self.api({"client_id": "cid123", "client_secret": "csec", "refresh_token": "rt"})
        tools = {t["name"]: t for t in tm.register(api)}
        tools["chats"]["handler"](None, {})
        self.assertEqual(captured["payload"].get("client_secret"), "csec")
        self.assertEqual(captured["payload"].get("client_id"), "cid123")

    def test_teams_tools_and_missing_config(self):
        tm = load_py("teams")
        tm.oauth2_token = lambda *a, **k: {"access_token": "t"}
        api = self.api({"client_id": "cid123", "refresh_token": "rt"})
        tools = {t["name"]: t for t in tm.register(api)}
        ctx = SimpleNamespace(bot={"id": "b1"})
        tools["chat_send"]["handler"](ctx, {"chat_id": "c9", "text": "hi"})
        call = api.calls[-1]
        self.assertIn("/me/chats/c9/messages", call["url"])
        self.assertEqual(call["json_body"]["body"], {"contentType": "text", "content": "hi"})
        tools["create_meeting"]["handler"](ctx, {"start": "2026-10-20T14:00:00", "end": "2026-10-20T14:30:00"})
        self.assertIn("/me/onlineMeetings", api.calls[-1]["url"])
        bare = {t["name"]: t for t in tm.register(self.api({}))}
        with self.assertRaises(RuntimeError):
            bare["chats"]["handler"](ctx, {})


class CadTests(unittest.TestCase):
    def api(self, values):
        return FakeApi(values)

    def test_onshape_basic_auth_and_urls(self):
        on = load_py("onshape")
        api = self.api({"access_key": "ak", "secret_key": "sk"})
        tools = {t["name"]: t for t in on.register(api)}
        self.assertEqual(set(tools), {"documents", "document", "workspaces", "elements", "create_document"})
        tools["documents"]["handler"](None, {})
        call = api.calls[-1]
        self.assertIn("/api/v9/documents", call["url"])
        import base64
        self.assertEqual(call["headers"]["Authorization"], "Basic " + base64.b64encode(b"ak:sk").decode())
        tools["elements"]["handler"](None, {"document_id": "d1", "workspace_id": "w1"})
        self.assertIn("/documents/d/d1/w/w1/elements", api.calls[-1]["url"])
        tools["create_document"]["handler"](None, {"name": "Bracket"})
        self.assertEqual(api.calls[-1]["json_body"], {"name": "Bracket"})
        bare = {t["name"]: t for t in on.register(self.api({}))}
        with self.assertRaises(RuntimeError):
            bare["documents"]["handler"](None, {})

    def test_onshape_build_specs(self):
        specs = build_python(ROOT / "plugins" / "onshape", {"id": "onshape", "name": "Onshape"})(None)
        by = {sp.name: sp for sp in specs}
        self.assertIsNotNone(by["onshape_create_document"].risk)
        self.assertTrue(by["onshape_documents"].read_only)

    def test_onshape_oauth_uses_body_not_basic(self):
        on = load_py("onshape")
        captured = {}

        def fake_token(c, pid, url, payload, headers=None, rotate_field="refresh_token"):
            captured.update(payload=dict(payload), headers=headers, url=url)
            return {"access_token": "t"}

        on.oauth2_token = fake_token
        import core.plugins as plugmod
        import unittest.mock as mock
        api = self.api({"client_id": "cid", "client_secret": "csec"})
        with mock.patch.object(plugmod.secrets, "get_secret", lambda n: {"plugin:onshape:refresh_token": "rt"}.get(n)):
            self.assertEqual(on._auth(api)["Authorization"], "Bearer t")
        self.assertEqual(captured["url"], "https://oauth.onshape.com/oauth/token")
        self.assertEqual(captured["payload"].get("client_id"), "cid")
        self.assertEqual(captured["payload"].get("client_secret"), "csec")
        self.assertIsNone(captured["headers"], "Onshape wants credentials in the body, not Basic")

    def test_sage_region_overrides(self):
        sg = load_py("sage")
        api = self.api({"token_url": "https://oauth.na.sageone.com/token", "api_base": "https://api.na.sageone.com/v3.1"})
        self.assertEqual(sg._token_url(api), "https://oauth.na.sageone.com/token")
        self.assertEqual(sg._api(api), "https://api.na.sageone.com/v3.1")
        bare = self.api({})
        self.assertEqual(sg._token_url(bare), sg.TOKEN_URL)
        self.assertEqual(sg._api(bare), sg.API)

    def test_fusion_browse_and_auth(self):
        fu = load_py("fusion")
        fu.oauth2_token = lambda *a, **k: {"access_token": "t"}
        api = self.api({"client_id": "c", "client_secret": "s", "refresh_token": "r"})
        tools = {t["name"]: t for t in fu.register(api)}
        self.assertEqual(set(tools), {"hubs", "projects", "top_folders", "folder_contents", "item_versions"})
        tools["projects"]["handler"](None, {"hub_id": "h1"})
        call = api.calls[-1]
        self.assertIn("/project/v1/hubs/h1/projects", call["url"])
        self.assertEqual(call["headers"], {"Authorization": "Bearer t"})
        tools["folder_contents"]["handler"](None, {"project_id": "p1", "folder_id": "f1"})
        self.assertIn("/data/v1/projects/p1/folders/f1/contents", api.calls[-1]["url"])
        bare = {t["name"]: t for t in fu.register(self.api({}))}
        with self.assertRaises(RuntimeError):
            bare["hubs"]["handler"](None, {})

    def test_circuitlab_skill_present(self):
        raw = (ROOT / "skills" / "circuitlab-web.md").read_text(encoding="utf-8")
        head = raw.split("---\n", 2)[1]
        self.assertIn("name: circuitlab-web", head)
        self.assertIn("status: active", head)
        self.assertIn("browser", raw.lower())
        self.assertNotIn("api key", raw.lower())


if __name__ == "__main__":
    unittest.main()
