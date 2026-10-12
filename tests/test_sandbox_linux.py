"""Linux sandbox (WSL2 Ubuntu): path mapping, fail-closed behaviour, worker protocol."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import sandbox_linux as linux  # noqa: E402


class Paths(unittest.TestCase):
    def test_win_to_wsl(self):
        self.assertEqual(linux.win_to_wsl(r"C:\x\y"), "/mnt/c/x/y")
        self.assertEqual(linux.win_to_wsl("D:/a b/c"), "/mnt/d/a b/c")
        self.assertEqual(linux.win_to_wsl("/already/posix"), "/already/posix")

    def test_quoted_escaping(self):
        self.assertEqual(linux.quoted("a'b"), "'a'\\''b'")


class FailClosed(unittest.TestCase):
    def test_no_wsl_is_an_error_not_a_windows_fallback(self):
        with mock.patch.object(linux, "available", return_value=(False, "nope")):
            d = linux.run("rm -rf /", workspace=r"C:\w")
        self.assertEqual(d["exit"], -1)
        self.assertIn("nope", d["output"])
        self.assertFalse(d["timed_out"])


class WorkerProtocol(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(linux, "available", return_value=(True, "Ubuntu"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _popen(self, **kw):
        pops = []

        class FakeProc:
            def __init__(self, *a, **k):
                self.args, self.kwargs = a, k
                pops.append(self)
                self.pid, self.returncode = 4242, 0

            def communicate(self, *a, **k):
                return kw.get("out", "hi\n"), ""

        return FakeProc, pops

    def test_argv_env_and_cwd(self):
        FakeProc, pops = self._popen()
        os.environ["ZB_SECRET_PROBE"] = "s3cr3t"
        try:
            with mock.patch.object(subprocess, "Popen", FakeProc):
                d = linux.run("echo hi", workspace=r"C:\w\space here", timeout=30)
        finally:
            del os.environ["ZB_SECRET_PROBE"]
        self.assertEqual(d["exit"], 0)
        p = pops[0]
        argv = p.args[0]
        self.assertTrue(str(argv[0]).lower().endswith("wsl.exe"))
        self.assertEqual(list(argv[1:6]), ["-d", "Ubuntu", "-u", "root", "--"])
        inner = p.args[0][-1]
        self.assertIn("HOME=/tmp/sb-", inner)
        self.assertIn("/mnt/c/w/space here", inner)
        self.assertIn("echo hi", inner)
        self.assertNotIn("ZB_SECRET_PROBE", p.kwargs["env"])
        self.assertIn("PATH", p.kwargs["env"])

    def test_timeout_kills(self):
        class SlowProc:
            def __init__(self, *a, **k):
                self.pid, self.returncode = 4343, None

            def communicate(self, *a, **k):
                raise subprocess.TimeoutExpired("wsl", 3)

            def wait(self, timeout=None):
                return 0

        killed = []
        real_run = subprocess.run

        def fake_run(*a, **k):
            killed.append(a[0][:3])
            return real_run(["cmd", "/c", "exit", "0"], capture_output=True)

        with mock.patch.object(subprocess, "Popen", SlowProc):
            with mock.patch.object(linux.subprocess, "run", fake_run):
                d = linux.run("sleep 30", workspace=r"C:\w", timeout=3)
        self.assertTrue(d["timed_out"])
        self.assertTrue(killed)

    def test_truncation(self):
        class LoudProc:
            pid, returncode = 4444, 0

            def __init__(self, *a, **k):
                pass

            def communicate(self, *a, **k):
                return "x" * 50000, ""

        with mock.patch.object(subprocess, "Popen", LoudProc):
            d = linux.run("yes", workspace=r"C:\w")
        self.assertLessEqual(len(d["output"]), 21000)
        self.assertIn("omitted", d["output"])


class Live(unittest.TestCase):
    def test_live_echo(self):
        ok, why = linux.available()
        if not ok:
            self.skipTest(why)
        import tempfile

        ws = tempfile.mkdtemp(prefix="zb-lin-")
        d = linux.run("echo hello-linux && pwd && whoami", workspace=ws, timeout=60)
        self.assertEqual(d["exit"], 0, d["output"])
        self.assertIn("hello-linux", d["output"])

    def test_live_disposable_home_and_clean_env(self):
        ok, why = linux.available()
        if not ok:
            self.skipTest(why)
        import tempfile

        ws = tempfile.mkdtemp(prefix="zb-lin-")
        d = linux.run("echo HOME=$HOME && touch ~/marker && ls ~ && printenv | grep -ci 'secret\\|token\\|password'; echo ---; bash --version | head -1",
                      workspace=ws, timeout=90)
        self.assertEqual(d["exit"], 0, d["output"])
        self.assertIn("HOME=/tmp/sb-", d["output"])
        self.assertIn("marker", d["output"])
        self.assertRegex(d["output"], r"\n0\n")


if __name__ == "__main__":
    unittest.main()
