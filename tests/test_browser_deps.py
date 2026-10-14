"""Self-healing browser system libraries: parsing, mapping, fail-closed behaviour."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import browser_deps  # noqa: E402

LDD = """linux-vdso.so.1 (0x00007ffc)
\tlibnss3.so => not found
\tlibnspr4.so => not found
\tlibatk-1.0.so.0 => /lib/x86_64-linux-gnu/libatk-1.0.so.0 (0x00007f)
\tlibfoo.so.2 => not found
"""


class Parsing(unittest.TestCase):
    def test_missing_libs_parsed(self):
        with mock.patch.object(browser_deps, "_sh", return_value=(0, LDD)):
            self.assertEqual(browser_deps.missing_for(Path("/x/headless_shell")),
                             ["libfoo.so", "libnspr4.so", "libnss3.so"])

    def test_ldd_failure_means_nothing_missing(self):
        with mock.patch.object(browser_deps, "_sh", return_value=(127, "nope")):
            self.assertEqual(browser_deps.missing_for(Path("/x")), [])

    def test_known_chromium_set_mapped(self):
        for lib in ("libnss3.so", "libnspr4.so", "libatk-1.0.so", "libgbm.so", "libasound.so", "libcups.so"):
            self.assertIn(lib, browser_deps.LIB2PKG, lib)


class FailClosed(unittest.TestCase):
    def test_non_trigger_text_never_touches_the_system(self):
        with mock.patch.object(browser_deps, "_sh", side_effect=AssertionError("must not run")):
            self.assertEqual(browser_deps.ensure_for_launch("Executable doesn't exist", Path("/tmp/x")), "")

    def test_non_debian_never_touches_the_system(self):
        with mock.patch.object(browser_deps, "is_debian_like", return_value=False):
            with mock.patch.object(browser_deps, "_sh", side_effect=AssertionError("must not run")):
                self.assertEqual(browser_deps.ensure_for_launch("error while loading shared libraries: libnss3.so", Path("/tmp/x")), "")

    def test_unmapped_lib_gives_up(self):
        with mock.patch.object(browser_deps, "is_debian_like", return_value=True), \
                mock.patch("shutil.which", return_value="/usr/bin/apt-get"), \
                mock.patch.object(browser_deps, "find_headless_shell", return_value=Path("/b/shell")), \
                mock.patch.object(browser_deps, "missing_for", return_value=["libweird.so"]):
            # shutil.which is imported inside ensure_for_launch? no: module-level shutil
            import shutil as _shutil
            with mock.patch.object(_shutil, "which", side_effect=lambda c: "/usr/bin/" + c if c in ("apt-get", "dpkg") else None):
                self.assertEqual(browser_deps.ensure_for_launch("libweird.so: cannot open", Path("/tmp/x")), "")


class FullFlow(unittest.TestCase):
    def test_fetch_extract_and_libdir(self):
        import tempfile

        dest = Path(tempfile.mkdtemp(prefix="zb-bdeps-"))
        deb = dest / "libnss3_1.deb"
        deb.write_bytes(b"fake-deb")

        def fake_run_apt(args, env, cwd=None):
            if args == ["update"]:
                return 0, "ok"
            (Path(cwd) / "libnss3_1.deb").write_bytes(b"fake-deb")
            return 0, "ok"

        extracted = []

        def fake_sh(*argv, **kw):
            if argv[0] == "dpkg":
                extracted.append(argv)
                (dest / "usr" / "lib" / "x86_64-linux-gnu").mkdir(parents=True, exist_ok=True)
                return 0, ""
            return 0, ""

        with mock.patch.object(browser_deps, "is_debian_like", return_value=True), \
                mock.patch.object(browser_deps, "find_headless_shell", return_value=Path("/b/shell")), \
                mock.patch.object(browser_deps, "missing_for", return_value=["libnss3.so"]), \
                mock.patch.object(browser_deps, "_run_apt", side_effect=fake_run_apt), \
                mock.patch.object(browser_deps, "_sh", side_effect=fake_sh):
            import shutil as _shutil
            with mock.patch.object(_shutil, "which", side_effect=lambda c: "/usr/bin/" + c if c in ("apt-get", "dpkg") else None):
                got = browser_deps.ensure_for_launch("error while loading shared libraries: libnss3.so", dest)
        self.assertEqual(got, str(dest / "usr" / "lib" / "x86_64-linux-gnu"))
        self.assertTrue(extracted)


if __name__ == "__main__":
    unittest.main()
