"""Sourcing plugins: DigiKey (python, OAuth2 client-credentials) + Mouser (declarative API key).
python -m unittest tests.test_sourcing -v"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-sourcing-"))

from core.plugins import build_declarative  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def load_manifest(pid):
    return json.loads((ROOT / "plugins" / pid / "plugin.json").read_text(encoding="utf-8"))


def load_py(pid):
    spec = importlib.util.spec_from_file_location(f"plug_{pid}", ROOT / "plugins" / pid / "plugin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MouserTests(unittest.TestCase):
    def _conn(self, resp):
        conn = SimpleNamespace(cfg=lambda k, d="": {"api_key": "sekrit"}.get(k, d))

        def fake_http(ctx, method, url, **kw):
            self.captured = {"method": method, "url": url, **kw}
            return resp
        conn.http = fake_http
        return conn

    def test_manifest_tools_and_secret_declared(self):
        m = load_manifest("mouser")
        self.assertEqual(m["id"], "mouser")
        names = {t["name"] for t in m["tools"]}
        self.assertEqual(names, {"search_keyword", "search_part_number"})
        self.assertTrue(all(f["key"] == "api_key" and f.get("secret") for f in m["fields"]))

    def test_keyword_wire(self):
        m = load_manifest("mouser")
        conn = self._conn({"SearchResults": {"Parts": [1, 2, 3]}})
        specs = build_declarative({**m, "id": "mouser"})(conn)
        spec = next(s for s in specs if s.name == "mouser_search_keyword")
        out = spec.handler(None, {"keyword": "LM7805", "records": 10})
        self.assertEqual(self.captured["method"], "POST")
        self.assertIn("keywordwithmfrnumber", self.captured["url"])
        self.assertEqual(self.captured["params"]["apiKey"], "sekrit")
        self.assertEqual(self.captured["json_body"]["SearchByKeywordRequest"]["keyword"], "LM7805")
        self.assertEqual(json.loads(out.text), [1, 2, 3])   # response_path passthrough

    def test_partnumber_wire(self):
        m = load_manifest("mouser")
        conn = self._conn({"SearchResults": {"Parts": [{"MfrPartNumber": "LM7805"}]}})
        specs = build_declarative({**m, "id": "mouser"})(conn)
        spec = next(s for s in specs if s.name == "mouser_search_part_number")
        spec.handler(None, {"part_number": "LM7805", "records": 10})
        self.assertIn("/api/v1/search/partnumber", self.captured["url"])
        self.assertEqual(self.captured["json_body"]["SearchByPartMfrRequest"]["partSearchRequestList"][0]["mfrNumber"], "LM7805")


class DigiKeyTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_py("digikey")

    def _api(self, responses):
        values = {"client_id": "cid", "client_secret": "cs"}
        queue = list(responses)

        class _Api:
            def config(self, key, default=""):
                return values.get(key, default)

            def http(self, ctx, method, url, **kw):
                self.calls.append({"method": method, "url": url, **kw})
                return queue.pop(0)
        api = _Api()
        api.calls = []
        tools = {t["name"]: t for t in self.mod.register(api)}
        return api, tools

    def test_tools_read_only(self):
        api, tools = self._api([])
        self.assertEqual(set(tools), {"search_keyword", "search_part_number"})

    def test_keyword_token_then_search(self):
        api, tools = self._api([
            {"access_token": "tok123", "expires_in": 300},
            {"TotalResults": 42, "Products": [{"ManufacturerPartNumber": "LM7805", "QuantityAvailable": 12345,
                                               "Manufacturer": {"Name": "Texas Instruments"}, "UnitPrice": 0.95,
                                               "ProductUrl": "https://digikey.com/x"}]},
        ])
        out = tools["search_keyword"]["handler"](None, {"keyword": "LM7805", "limit": 5})
        token_call, search_call = api.calls[0], api.calls[1]
        self.assertTrue(token_call["url"].endswith("/oauth2/token"))
        self.assertEqual(token_call["data"]["grant_type"], "client_credentials")
        self.assertEqual(token_call["data"]["client_id"], "cid")
        self.assertEqual(search_call["headers"]["Authorization"], "Bearer tok123")
        self.assertEqual(search_call["headers"]["X-DIGIKEY-Client-Id"], "cid")
        self.assertEqual(search_call["json_body"], {"Keywords": "LM7805", "Limit": 5})
        self.assertEqual(out["total"], 42)
        self.assertEqual(out["products"][0]["part_number"], "LM7805")
        self.assertEqual(out["products"][0]["stock"], 12345)
        self.assertEqual(out["products"][0]["unit_price"], 0.95)

    def test_token_cached_between_calls(self):
        api, tools = self._api([
            {"access_token": "tokA", "expires_in": 300},
            {"Products": []},
            {"Products": []},
        ])
        tools["search_keyword"]["handler"](None, {"keyword": "x"})
        tools["search_keyword"]["handler"](None, {"keyword": "y"})
        self.assertEqual(len(api.calls), 3)   # one token fetch, two searches
        self.assertEqual(api.calls[2]["headers"]["Authorization"], "Bearer tokA")


if __name__ == "__main__":
    unittest.main()
