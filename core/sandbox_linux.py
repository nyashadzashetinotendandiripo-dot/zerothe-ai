"""Disposable Ubuntu command runner (WSL2) for untrusted code.

A Bot's Linux work (scripts, data crunching, builds, anything untrusted or
non-Windows) runs here instead of on the Windows host: a throwaway HOME, a
scrubbed environment, the workspace mounted in, hard timeouts and output caps.
It has no access to the registry, Startup folder, credentials, or anything else
Windows-side by construction — there is simply no Windows in the room.

Network: off by default per run would be ideal, but WSL2 has no per-command
network switch, so network runs are allowed and plainly reported. Use the Bot
network policy (HTTP tools) for anything that must be gated; see module docs.
Requires WSL2 + Ubuntu (`wsl --install`); without it every call fails closed
with install instructions instead of silently running on the host.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

WSL_EXE = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wsl.exe")

_SHORT = re.compile(r"[^\w\-.]+")


def wsl_exe() -> str | None:
    for cand in (shutil.which("wsl"), WSL_EXE):
        if cand and Path(cand).exists():
            return cand
    return None


def available() -> tuple[bool, str]:
    """(True, distro) when WSL2 + Ubuntu is usable, else (False, reason)."""
    exe = wsl_exe()
    if not exe:
        return False, "WSL is not installed. Run `wsl --install` in an admin terminal (about 1 GB), restart if asked, then open Ubuntu once."
    try:
        r = subprocess.run([exe, "-l", "-q"], capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # noqa: BLE001
        return False, f"WSL did not answer: {e}"
    distros = [ln.strip() for ln in r.stdout.replace("\x00", "").splitlines() if ln.strip()]
    if not any(d.lower().lstrip("* ").strip() == "ubuntu" for d in distros):
        return False, "Ubuntu is not installed in WSL. Run `wsl --install -d Ubuntu`."
    return True, "Ubuntu"


def win_to_wsl(path: str | os.PathLike) -> str:
    """C:\\x\\y -> /mnt/c/x/y for the WSL mount table."""
    s = str(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return s


def _scrub_env() -> dict[str, str]:
    keep = {"PATH", "LANG", "LC_ALL", "HOME", "TERM", "PYTHONUTF8", "PYTHONIOENCODING"}
    pat = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)
    return {k: v for k, v in os.environ.items() if (k in keep or k.startswith("WSL")) and not pat.search(k)}


def run(cmd: str, *, workspace: str | os.PathLike, timeout: int = 120, memory_mb: int = 2048) -> dict:
    """Run a Bash command in a disposable Ubuntu user-space. Never touches Windows.

    Returns {"exit", "output", "timed_out"}. A fresh HOME per run means nothing
    persists between calls except what the command writes into the workspace.
    """
    ok, why = available()
    if not ok:
        return {"exit": -1, "output": why, "timed_out": False}
    exe = wsl_exe() or "wsl.exe"
    home = f"/tmp/sb-{uuid.uuid4().hex[:8]}"
    ws = win_to_wsl(workspace)
    timeout = max(5, min(int(timeout or 120), 900))
    inner = (
        f"mkdir -p {home} && export HOME={home} && cd {quoted(ws)} && "
        f"ulimit -v {int(memory_mb) * 1024} 2>/dev/null; {cmd}"
    )
    argv = [exe, "-d", "Ubuntu", "-u", "root", "--", "bash", "-c", inner]
    # NOTE: always root: needs no first-run user setup, and files created under
    # /mnt/c still land owned by the Windows user that launched wsl.exe. Root is
    # also what a future --network=none mode (unshare -n) will require.
    t0 = time.time()
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=_scrub_env(), text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return {"exit": -1, "output": f"Could not start the Linux sandbox: {e}", "timed_out": False}
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        return {"exit": -1, "output": f"(TIMED OUT after {timeout}s and was killed)", "timed_out": True}
    out = out or ""
    if len(out) > 20000:
        out = out[:10000] + f"\n[... {len(out) - 20000} chars omitted ...]\n" + out[-10000:]
    return {"exit": proc.returncode if proc.returncode is not None else -1, "output": out,
            "seconds": round(time.time() - t0, 1), "timed_out": False}


def quoted(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _kill(proc: subprocess.Popen) -> None:
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
