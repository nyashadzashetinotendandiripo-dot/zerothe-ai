"""InstaBox cloud backend: client behaviour with a faked transport (no live machines, no spend)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import sandbox_cloud as cloud  # noqa: E402


class FakeTransport:
    """Scripted InstaBox API: create -> done op, exec -> canned result, delete logged."""

    def __init__(self, exec_result=None, fail_exec=False):
        self.calls: list = []
        self.exec_result = exec_result if exec_result is not None else {"stdout": "hi-cloud\n", "stderr": "", "exitCode": 0}
        self.fail_exec = fail_exec

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "/v1/machines" and method == "POST":
            return {"id": "op_1", "machineId": "m_1", "state": "done"}
        if path == "/v1/operations/op_1":
            return {"id": "op_1", "machineId": "m_1", "state": "succeeded"}
        if path == "/v1/machines/m_1/exec":
            if self.fail_exec:
                raise cloud.InstaBoxError("boom")
            return dict(self.exec_result)
        if path == "/v1/machines/m_1" and method == "DELETE":
            return {"id": "op_2", "state": "done"}
        raise AssertionError("unexpected call %s %s" % (method, path))


def run_with(transport, **kw):
    kw.setdefault("timeout", 120)
    with mock.patch.object(cloud.Client, "call", autospec=True) as m:
        m.side_effect = lambda self, method, path, body=None: transport(method, path, body)
        with mock.patch.dict("os.environ", {"INSTABOX_API_KEY": "ibk_test"}):
            return cloud.run("echo hi", **kw)


class CloudBackend(unittest.TestCase):
    def test_round_trip_and_cleanup(self):
        t = FakeTransport()
        d = run_with(t)
        self.assertEqual(d["exit"], 0)
        self.assertIn("hi-cloud", d["output"])
        self.assertFalse(d["timed_out"])
        methods = [m for m, _ in t.calls]
        self.assertIn("DELETE", methods)   # machine always cleaned up
        self.assertEqual(t.calls[0], ("POST", "/v1/machines"))

    def test_create_body_defaults(self):
        bodies: list = []
        orig = FakeTransport.__call__

        def spy(self, method, path, body=None):
            if path == "/v1/machines":
                bodies.append(body)
            return orig(self, method, path, body)

        with mock.patch.object(FakeTransport, "__call__", spy):
            run_with(FakeTransport())
        b = bodies[0]
        self.assertEqual(b["templateId"], "universal")
        self.assertEqual(b["onTimeout"], "kill")
        self.assertTrue(b["timeoutMs"] > 0)
        self.assertEqual(b["metadata"]["owner"], "zerothebot-run")

    def test_exec_error_is_result_not_crash(self):
        d = run_with(FakeTransport(fail_exec=True))
        self.assertEqual(d["exit"], -1)
        self.assertIn("boom", d["output"])

    def test_no_key_fails_closed(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            env = {k: v for k, v in __import__("os").environ.items() if k != "INSTABOX_API_KEY"}
            with mock.patch.dict("os.environ", env, clear=True):
                d = cloud.run("echo hi", timeout=30)
        self.assertEqual(d["exit"], -1)
        self.assertIn("INSTABOX_API_KEY", d["output"])

    def test_output_truncation(self):
        big = "x" * 50000
        d = run_with(FakeTransport(exec_result={"stdout": big, "stderr": "", "exitCode": 0}))
        self.assertLessEqual(len(d["output"]), 21000)
        self.assertIn("omitted", d["output"])

    def test_error_mapping(self):
        c = cloud.Client("k")
        with mock.patch("urllib.request.urlopen", side_effect=Exception("dns down")):
            with self.assertRaises(cloud.InstaBoxError):
                c.call("GET", "/v1/usage")


if __name__ == "__main__":
    unittest.main()
