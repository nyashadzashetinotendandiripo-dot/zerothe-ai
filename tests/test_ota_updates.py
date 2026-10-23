"""Self-hosted OTA update endpoints (expo-updates protocol)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-ota-"))

from fastapi.testclient import TestClient  # noqa: E402

from core import paths  # noqa: E402
from core.engine import Engine  # noqa: E402
from service.server import create_app  # noqa: E402

TOKEN = "ota-test-token"


class OtaUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = Engine()
        cls.eng.start = lambda: None
        cls.app = create_app(cls.eng, TOKEN)
        cls.c = TestClient(cls.app)

    def setUp(self):
        self.dir = paths.data_dir() / "updates"
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in self.dir.glob("*"):
            if f.is_file():
                f.unlink()
        m = self.dir / "manifest.json"
        if m.exists():
            m.unlink()

    def test_manifest_404_before_any_publish(self):
        self.assertEqual(self.c.get("/updates/manifest").status_code, 404)

    def test_manifest_served_with_protocol_headers(self):
        man = {"id": "11111111-1111-1111-1111-111111111111", "createdAt": "2026-01-01T00:00:00Z",
               "runtimeVersion": "1",
               "launchAsset": {"key": "bundle", "contentType": "application/javascript",
                               "url": "https://example.test/updates/assets/abc.hbc"},
               "assets": [], "metadata": {}, "extra": {}}
        (self.dir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        r = self.c.get("/updates/manifest", headers={"expo-platform": "android", "expo-runtime-version": "1",
                                                    "expo-protocol-version": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["expo-protocol-version"], "1")
        self.assertEqual(r.json()["runtimeVersion"], "1")

    def test_asset_served_and_traversal_blocked(self):
        (self.dir / "assets").mkdir(exist_ok=True)
        (self.dir / "assets" / "abc.hbc").write_bytes(b"hbc-data")
        r = self.c.get("/updates/assets/abc.hbc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"hbc-data")
        self.assertEqual(self.c.get("/updates/assets/..%2Fmanifest.json").status_code, 404)


if __name__ == "__main__":
    unittest.main()
