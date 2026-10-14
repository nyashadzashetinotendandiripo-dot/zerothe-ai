"""Self-healing Chromium system libraries for rootless Linux hosts.

Playwright's browser needs ~20 system libraries (libnss3, libatk, ...) that
normally arrive via `apt install` — impossible without root on hosted
containers. But `apt-get download` + `dpkg -x` need no privileges at all, so on
a missing-.so launch failure we fetch exactly the absent packages into our own
data folder and point the loader at them with LD_LIBRARY_PATH.

Nothing here runs at import or boot: `ensure_for_launch()` is called only from
the browser launch failure path, and every step fails closed (returns "").
Debian/Ubuntu guests only; anything else gets a clear message.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SO_RE = re.compile(r"(\S+\.so(?:\.\d+)*)\s*=>\s*not found")

# shared library -> Debian package(s) providing it (Chromium's runtime set)
LIB2PKG = {
    "libnss3.so": ["libnss3"],
    "libnspr4.so": ["libnspr4"],
    "libatk-1.0.so": ["libatk1.0-0"],
    "libatk-bridge-2.0.so": ["libatk-bridge2.0-0"],
    "libatspi.so": ["libatspi2.0-0"],
    "libcups.so": ["libcups2"],
    "libdrm.so": ["libdrm2"],
    "libxkbcommon.so": ["libxkbcommon0"],
    "libxcomposite.so": ["libxcomposite1"],
    "libxdamage.so": ["libxdamage1"],
    "libxfixes.so": ["libxfixes3"],
    "libxrandr.so": ["libxrandr2"],
    "libgbm.so": ["libgbm1"],
    "libasound.so": ["libasound2"],
    "libpango-1.0.so": ["libpango-1.0-0"],
    "libcairo.so": ["libcairo2"],
    "libx11.so": ["libx11-6"],
    "libxcb.so": ["libxcb1"],
    "libxext.so": ["libxext6"],
    "libxi.so": ["libxi6"],
    "libxtst.so": ["libxtst6"],
    "libglib-2.0.so": ["libglib2.0-0"],
    "libdbus-1.so": ["libdbus-1-3"],
    "libexpat.so": ["libexpat1"],
}


def _sh(*argv: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 127, str(e)


def is_debian_like() -> bool:
    try:
        txt = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return "debian" in txt or "ubuntu" in txt


def find_headless_shell() -> Path | None:
    roots = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if env:
        roots.append(Path(env))
    roots.append(Path.home() / ".cache" / "ms-playwright")
    for base in roots:
        try:
            hits = sorted(base.glob("chromium-*/chrome-linux/headless_shell"))
        except OSError:
            continue
        for h in hits:
            if h.is_file():
                return h
    return None


def missing_for(binary: Path) -> list[str]:
    """Shared libraries `binary` needs but the loader cannot find."""
    rc, out = _sh("ldd", str(binary))
    if rc != 0:
        return []
    return sorted({m.group(1).split(".so")[0] + ".so" for m in SO_RE.finditer(out)})


def ensure_for_launch(error_text: str, dest: Path) -> str:
    """Fetch missing Chromium system libs into `dest`; return an LD_LIBRARY_PATH dir or "".

    Only triggers on missing-shared-library launch failures, Debian/Ubuntu
    guests only, everything best-effort. Safe to call repeatedly (skips work
    when nothing is missing).
    """
    if "shared librar" not in error_text and ".so" not in error_text:
        return ""
    if not is_debian_like():   # also False on Windows/macOS (no /etc/os-release there)
        return ""
    if shutil.which("apt-get") is None or shutil.which("dpkg") is None:
        return ""
    shell = find_headless_shell()
    if shell is None:
        return ""
    missing = missing_for(shell)
    if not missing:
        return ""
    pkgs: list[str] = []
    for lib in missing:
        pkgs.extend(LIB2PKG.get(lib, []))
    pkgs = list(dict.fromkeys(pkgs))
    if not pkgs:
        return ""
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="zb-apt-") as tmp:
            apt_env = dict(os.environ)
            apt_env["APT_CONFIG"] = _apt_conf(tmp)
            rc, _ = _run_apt(["update"], apt_env)
            if rc != 0:
                return ""
            for pkg in pkgs:
                rc, _ = _run_apt(["download", pkg], apt_env, cwd=tmp)
                if rc != 0:
                    continue
            debs = list(Path(tmp).glob("*.deb"))
            if not debs:
                return ""
            for deb in debs:
                _sh("dpkg", "-x", str(deb), str(dest), timeout=120)
    except Exception:  # noqa: BLE001
        return ""
    for cand in (dest / "usr" / "lib" / "x86_64-linux-gnu", dest / "usr" / "lib"):
        if cand.is_dir():
            return str(cand)
    return ""


def _apt_conf(tmp: str) -> str:
    """APT config redirecting writable state into tmp; system source lists are only read."""
    p = Path(tmp) / "apt.conf"
    p.write_text('Dir::State::Lists "%s";\nDir::Cache "%s";\n' % (tmp, tmp), encoding="utf-8")
    return str(p)


def _run_apt(args: list[str], env: dict, cwd: str | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(["apt-get", *args], capture_output=True, text=True, timeout=300,
                           cwd=cwd or str(Path.cwd()), env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 127, str(e)
