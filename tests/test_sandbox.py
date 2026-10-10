"""Plugin sandbox (L1 containment): scrubbed env, cwd jail, timeouts, worker protocol."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import sandbox  # noqa: E402
from core.sandbox_worker import _check_layers  # noqa: E402

PLUGIN_PY = '''
def register(api):
    def echo(args):
        return {"text": "echo:" + str(args.get("v", ""))}

    def whoami(args):
        import os
        return {"text": "",
                "data": __import__("json").dumps({
                    "pid": os.getpid(),
                    "cwd": os.getcwd(),
                    "probe": os.environ.get("ZEROTHEBOT_SECRET_PROBE_XYZ"),
                    "path_kept": bool(os.environ.get("PATH")),
                    "tmproot": bool(os.environ.get("SystemRoot") or os.environ.get("HOME")),
                })}

    def sleeper(args):
        import time
        time.sleep(30)
        return {"text": "should never arrive"}

    def needy(args):
        return {"text": "cfg:" + api.config("greeting", "?")}

    def boom(args):
        raise RuntimeError("kaput")

    return [{"name": "echo", "handler": echo}, {"name": "whoami", "handler": whoami},
            {"name": "sleeper", "handler": sleeper}, {"name": "needy", "handler": needy},
            {"name": "boom", "handler": boom}]
'''

LAYERS_OPEN = [{"name": "global policy", "mode": "open", "allow": [], "deny": [], "private": True}]


def fixture() -> Path:
    d = Path(tempfile.mkdtemp(prefix="zb-sandbox-"))
    (d / "plugin.py").write_text(PLUGIN_PY, encoding="utf-8")
    (d / "plugin.json").write_text(json.dumps({"id": "t", "name": "T"}), encoding="utf-8")
    return d


def call(tool: str, ws: Path, fix: Path, **kw) -> dict:
    return sandbox.run_tool(plugin_dir=str(fix), tool=tool, args=kw.pop("args", {}), bot={"id": "b1"},
                            thread_id="t1", turn_id="r1", config=kw.pop("config", {}), net_layers=list(LAYERS_OPEN),
                            workspace=str(ws), timeout=kw.pop("timeout", 60))


class Scrub(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(os.environ)
        os.environ["SystemRoot"] = r"C:\Windows"
        os.environ["ZEROTHEBOT_SECRET_PROBE_XYZ"] = "s3cr3t"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def test_secrets_dropped_essentials_kept(self):
        env = sandbox.scrub_env()
        self.assertNotIn("ZEROTHEBOT_SECRET_PROBE_XYZ", env)
        self.assertIn("PATH", env)
        # preservation, not host specifics: whatever really exists must survive the scrub
        for k in ("SystemRoot", "USERPROFILE"):
            self.assertEqual(k in env, os.environ.get(k) is not None, k)

    def test_isolation_policy(self):
        self.assertTrue(sandbox.should_isolate({"id": "anything"}))
        self.assertFalse(sandbox.should_isolate({"id": "x", "needs_engine": True}))
        self.assertFalse(sandbox.should_isolate({"id": "whatsapp"}))


class Worker(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(os.environ)
        os.environ["SystemRoot"] = r"C:\Windows"
        os.environ["ZEROTHEBOT_SECRET_PROBE_XYZ"] = "s3cr3t"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def test_round_trip_and_separate_process(self):
        fix, ws = fixture(), Path(tempfile.mkdtemp(prefix="zb-ws-"))
        d = call("echo", ws, fix, args={"v": "hi"})
        self.assertEqual(d["text"], "echo:hi")
        self.assertFalse(d["is_error"])
        d = call("whoami", ws, fix)
        info = json.loads(d["data"])
        self.assertNotEqual(info["pid"], os.getpid())
        self.assertEqual(Path(info["cwd"]), ws)
        self.assertIsNone(info["probe"])
        self.assertTrue(info["path_kept"])
        self.assertEqual(info["tmproot"], os.environ.get("SystemRoot") is not None or os.environ.get("USERPROFILE") is not None
                         or os.environ.get("HOME") is not None)

    def test_config_reaches_worker(self):
        fix, ws = fixture(), Path(tempfile.mkdtemp(prefix="zb-ws-"))
        d = call("needy", ws, fix, config={"greeting": "Yo"})
        self.assertEqual(d["text"], "cfg:Yo")

    def test_timeout_kills_tree(self):
        fix, ws = fixture(), Path(tempfile.mkdtemp(prefix="zb-ws-"))
        t0 = time.time()
        d = call("sleeper", ws, fix, timeout=3)
        self.assertTrue(d["is_error"])
        self.assertIn("timed out", d["text"])
        self.assertLess(time.time() - t0, 20)

    def test_crash_becomes_error_result(self):
        fix, ws = fixture(), Path(tempfile.mkdtemp(prefix="zb-ws-"))
        d = call("boom", ws, fix)
        self.assertTrue(d["is_error"])
        self.assertIn("kaput", d["text"])

    def test_missing_tool_and_bad_plugin(self):
        fix, ws = fixture(), Path(tempfile.mkdtemp(prefix="zb-ws-"))
        d = call("nope", ws, fix)
        self.assertTrue(d["is_error"])
        empty = Path(tempfile.mkdtemp(prefix="zb-empty-"))
        d = sandbox.run_tool(plugin_dir=str(empty), tool="x", args={}, bot={}, thread_id="", turn_id="",
                             config={}, net_layers=[], workspace=str(ws), timeout=30)
        self.assertTrue(d["is_error"])


class NetLayers(unittest.TestCase):
    def test_open_allows_public(self):
        self.assertTrue(_check_layers(LAYERS_OPEN, "https://example.com/x")[0])

    def test_private_blocked(self):
        ok, why = _check_layers(LAYERS_OPEN, "http://127.0.0.1:9/")
        self.assertFalse(ok)
        self.assertIn("private", why)

    def test_allowlist_and_deny(self):
        allow = [{"name": "p", "mode": "allowlist", "allow": ["*.example.com"], "deny": [], "private": False}]
        self.assertTrue(_check_layers(allow, "https://a.example.com/")[0])
        self.assertFalse(_check_layers(allow, "https://evil.com/")[0])
        deny = [{"name": "p", "mode": "open", "allow": [], "deny": ["evil.com"], "private": False}]
        self.assertFalse(_check_layers(deny, "https://evil.com/")[0])
        self.assertFalse(_check_layers([{"name": "p", "mode": "open", "allow": [], "deny": [], "private": False}],
                                        "ftp://x.com/")[0])


if __name__ == "__main__":
    unittest.main()
