"""bwrap command sandbox: argv construction, policy gating, run wiring.
python -m unittest tests.test_sandbox_bwrap
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-bwrap-")

from core import sandbox_bwrap as sb  # noqa: E402
from core.engine import Engine  # noqa: E402


def _sh() -> str:
    return "/usr/bin/sh" if Path("/usr/bin/sh").exists() else "/bin/sh"


class WrapArgvTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="ztbws-")).resolve()
        self.cwd = self.ws / "work"
        self.cwd.mkdir()
        self._bwrap = mock.patch.object(sb.shutil, "which", return_value="/usr/bin/bwrap")
        self._bwrap.start()
        self.addCleanup(self._bwrap.stop)

    def _wrap(self, command="echo hi", **kw):
        kw.setdefault("cwd", self.cwd)
        kw.setdefault("workspace", self.ws)
        with mock.patch.object(sb, "backend", return_value=("user", "")):
            return sb.wrap(command, **kw)

    def test_user_tier_structure(self):
        argv = self._wrap("echo hi")
        self.assertEqual(argv[0], "/usr/bin/bwrap")           # no sudo prefix
        self.assertEqual(argv[-3:], [_sh(), "-c", "echo hi"])
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--die-with-parent", argv)
        self.assertNotIn("--unshare-net", argv)               # network shared by default
        i = argv.index("--tmpfs")
        self.assertEqual(argv[i + 1], "/")                    # host root replaced by tmpfs
        b = argv.index("--bind")
        self.assertEqual(argv[b + 1], argv[b + 2])
        self.assertEqual(Path(argv[b + 1]), self.ws)          # workspace read-write at its real path
        c = argv.index("--chdir")
        self.assertEqual(Path(argv[c + 1]), self.cwd)
        self.assertEqual(argv.count("--bind"), 1)             # cwd inside workspace: no extra bind
        pairs = [argv[j:j + 2] for j in range(len(argv)) if argv[j] == "--ro-bind"]
        self.assertIn(["--ro-bind", "/proc"], pairs)          # host /proc bound read-only (user tier)
        self.assertNotIn(["--ro-bind", "/data"], pairs)

    def test_sudo_tier_structure(self):
        with mock.patch.object(sb, "backend", return_value=("sudo", "")):
            argv = sb.wrap("echo hi", cwd=self.cwd, workspace=self.ws, network="none")
        self.assertEqual(argv[:3], ["sudo", "-n", "/usr/bin/bwrap"])
        self.assertIn(["--proc", "/proc"], [argv[j:j + 2] for j in range(len(argv)) if argv[j] == "--proc"])
        self.assertIn("--unshare-net", argv)
        self.assertIn("--die-with-parent", argv)
        self.assertIn("setpriv", " ".join(argv))              # drops back to the caller's uid
        self.assertEqual(argv[-1], "echo hi")                 # command is a single argv item

    def test_wrap_network_none(self):
        self.assertIn("--unshare-net", self._wrap(network="none"))

    def test_wrap_outside_cwd_gets_own_bind(self):
        outside = Path(tempfile.mkdtemp(prefix="ztbout-")).resolve()
        argv = self._wrap(cwd=outside)
        self.assertEqual(argv.count("--bind"), 2)
        b = argv.index("--bind", argv.index("--bind") + 1)
        self.assertEqual(Path(argv[b + 1]), outside)

    def test_wrap_hides_host_paths(self):
        home = Path(os.environ["ZEROTHEBOT_HOME"]).resolve()
        argv = self._wrap()
        joined = " ".join(argv)
        self.assertNotIn(str(home), joined)                   # service home (data, account, keys) never mounted
        self.assertIn(str(self.ws), joined)                   # workspace is

    def test_wrap_without_backend_raises(self):
        with mock.patch.object(sb, "backend", return_value=(None, "bubblewrap is not installed")):
            with self.assertRaises(FileNotFoundError):
                sb.wrap("true", cwd=self.cwd, workspace=self.ws)

    def test_available_on_windows_is_false(self):
        with mock.patch.object(sb, "IS_WIN", True):
            self.assertEqual(sb.available(), (False, "bwrap sandboxes need a Linux host."))

    def test_backend_cache(self):
        sb.reset_cache()
        with mock.patch.object(sb, "IS_WIN", False), mock.patch.object(sb, "_detect", return_value=("user", "ok")) as d:
            self.assertEqual(sb.backend(), ("user", "ok"))
            self.assertEqual(sb.backend(), ("user", "ok"))
            self.assertEqual(d.call_count, 1)                  # positive result cached
        sb.reset_cache()
        with mock.patch.object(sb, "IS_WIN", False), mock.patch.object(sb, "_detect", return_value=(None, "nope")) as d:
            self.assertEqual(sb.backend(), (None, "nope"))
            self.assertEqual(d.call_count, 1)                  # negative result cached too
        sb.reset_cache()


@unittest.skipUnless(os.name != "nt" and shutil.which("bwrap"), "needs a Linux host with bwrap")
class RealBwrapTests(unittest.TestCase):
    def test_host_is_hidden_and_workspace_visible(self):
        ws = Path(tempfile.mkdtemp(prefix="ztbreal-")).resolve()
        (ws / "in.txt").write_text("inside-ok", encoding="utf-8")
        host_home = Path("/home")
        argv = sb.wrap("cat in.txt && echo HOME=$HOME && ls /home 2>&1 | head -2", cwd=ws, workspace=ws)
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("inside-ok", p.stdout)
        self.assertIn("HOME=/tmp/ztb-home", p.stdout)
        if host_home.is_dir() and any(host_home.iterdir()):
            # /home exists on the host with entries, yet is invisible inside the jail
            self.assertIn("No such file", p.stdout)


class RunCommandSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = Engine()

    def setUp(self):
        self.eng.settings.set("computer.sandbox", "auto")
        self.eng.settings.set("computer.sandbox_network", "shared")

    def tearDown(self):
        self.eng.settings.set("computer.sandbox", "auto")
        sb.reset_cache()

    def test_auto_unavailable_runs_plain(self):
        with mock.patch.object(sb, "available", return_value=(False, "no bwrap here")):
            res = self.eng.computer.run_command("echo plain-run")
        self.assertEqual(res["sandbox"], "off")
        self.assertIn("plain-run", res["output"])
        self.assertEqual(res["exit"], 0)

    def test_auto_available_runs_wrapped(self):
        fake = [sys.executable, "-c", "print('inside-wrapper')"]
        with mock.patch.object(sb, "available", return_value=(True, "bwrap available")), \
                mock.patch.object(sb, "wrap", return_value=fake) as w:
            res = self.eng.computer.run_command("whatever", cwd=".")
        self.assertEqual(res["sandbox"], "bwrap")
        self.assertIn("inside-wrapper", res["output"])
        self.assertEqual(res["exit"], 0)
        self.assertTrue(w.call_args.kwargs.get("network") in ("shared", "none"))
        last = list(self.eng.computer.terminal_log)[-1]
        self.assertEqual(last["sandbox"], "bwrap")

    def test_bwrap_required_fails_closed(self):
        self.eng.settings.set("computer.sandbox", "bwrap")
        with mock.patch.object(sb, "available", return_value=(False, "bubblewrap is not installed")):
            res = self.eng.computer.run_command("echo must-not-run")
        self.assertEqual(res["exit"], -1)
        self.assertEqual(res["sandbox"], "unavailable")
        self.assertIn("sandbox unavailable", res["output"].lower())
        self.assertNotIn("must-not-run", res["output"])

    def test_off_never_wraps(self):
        self.eng.settings.set("computer.sandbox", "off")
        with mock.patch.object(sb, "wrap", side_effect=AssertionError("must not wrap")):
            res = self.eng.computer.run_command("echo no-sandbox-please")
        self.assertEqual(res["sandbox"], "off")
        self.assertIn("no-sandbox-please", res["output"])

    def test_network_setting_reaches_wrap(self):
        self.eng.settings.set("computer.sandbox_network", "none")
        with mock.patch.object(sb, "available", return_value=(True, "ok")), \
                mock.patch.object(sb, "wrap", return_value=[sys.executable, "-c", "print(1)"]) as w:
            self.eng.computer.run_command("true")
        self.assertEqual(w.call_args.kwargs.get("network"), "none")

    def test_status_reports_sandbox(self):
        with mock.patch.object(sb, "backend", return_value=("sudo", "bwrap available (fresh /proc)")):
            d = self.eng.computer.status()
        s = d["sandbox"]
        self.assertEqual(s["mode"], "auto")
        self.assertTrue(s["available"])
        self.assertEqual(s["backend"], "sudo")
        self.assertTrue(s["active"])
        self.assertEqual(s["network"], "shared")

    def test_status_inactive_when_off(self):
        self.eng.settings.set("computer.sandbox", "off")
        with mock.patch.object(sb, "backend", return_value=("user", "bwrap available (user namespaces)")):
            d = self.eng.computer.status()
        self.assertFalse(d["sandbox"]["active"])
        self.assertEqual(d["sandbox"]["backend"], "user")


if __name__ == "__main__":
    unittest.main()
