"""bubblewrap (bwrap) command sandbox for Linux hosts.

Shell commands run inside a namespace jail: the host root is replaced by an
empty tmpfs that mounts only read-only system directories plus the workspace
(read-write), inside a private PID namespace, with a throwaway $HOME and /tmp.
The host filesystem (service home, data, credentials) is simply not mounted,
a command cannot address host processes (PIDs are namespace-relative), and
host /proc entries cannot be read. Network is shared by default; set
``computer.sandbox_network = "none"`` to cut it.

Two backends are probed, best first, and the result is cached (positive
5 min, negative 1 min):

* ``sudo`` — ``sudo -n bwrap ... --proc`` gives a *fresh* /proc holding only
  the jail's own processes; a static ``setpriv`` shim then drops back to the
  calling uid before the command runs, so workspace files keep their owner.
  Needs passwordless sudo (true on most cloud images).
* ``user`` — plain unprivileged bwrap with ``--unshare-pid`` and the host
  /proc bound read-only: same filesystem/network/PID isolation, but ``ps``
  shows host processes (signals to them still fail: PIDs are ns-relative).

When bwrap is unavailable, ``computer.sandbox = "auto"`` runs commands plain
and ``"bwrap"`` refuses to run them at all.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

IS_WIN = os.name == "nt"

# Read-only system trees mounted into the jail. /etc carries resolv.conf,
# hosts and CA bundles so network tools still work; everything else (home,
# /data apart from the workspace, /root, other users) is never mounted.
RO_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc", "/opt")

HOME_DIR = "/tmp/ztb-home"
_POSITIVE_TTL = 300.0
_NEGATIVE_TTL = 60.0
_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "backend": None, "why": ""}


def backend() -> tuple[str | None, str]:
    """("sudo" | "user", why) or (None, why). Probe result is cached."""
    if IS_WIN:
        return None, "bwrap sandboxes need a Linux host."
    now = time.time()
    with _lock:
        ttl = _POSITIVE_TTL if _cache["backend"] else _NEGATIVE_TTL
        if now - _cache["ts"] < ttl:
            return _cache["backend"], _cache["why"]
    name, why = _detect()
    with _lock:
        _cache.update(ts=time.time(), backend=name, why=why)
    return name, why


def available() -> tuple[bool, str]:
    name, why = backend()
    return name is not None, why


def reset_cache() -> None:
    """Forget the probe result (tests, and after installing/removing bwrap)."""
    with _lock:
        _cache.update(ts=0.0, backend=None, why="")


def _drop_stmt() -> str:
    """Root prelude (tier sudo): fix /tmp + $HOME ownership, then drop to the caller's uid."""
    u = os.getuid() if hasattr(os, "getuid") else 1000   # Windows has no getuid; tier sudo never runs there
    g = os.getgid() if hasattr(os, "getgid") else 1000
    return (f"chmod 1777 /tmp && chown {u}:{g} {HOME_DIR} && chmod 700 {HOME_DIR} && "
            f"exec /usr/bin/setpriv --reuid={u} --regid={g} --init-groups /bin/sh -c \"$1\"")


def _static_flags(network: str, sudo: bool) -> list[str]:
    """Flags common to probes and real commands: root, system dirs, pid ns."""
    argv = ["--tmpfs", "/"]
    for d in RO_DIRS:
        if Path(d).exists():
            argv += ["--ro-bind", d, d]
    argv += (["--proc", "/proc"] if sudo else ["--ro-bind", "/proc", "/proc"])
    argv += ["--dev", "/dev", "--tmpfs", "/tmp", "--unshare-pid", "--die-with-parent"]
    if network == "none":
        argv.append("--unshare-net")
    return argv


def _probe(b: str, *, sudo: bool) -> tuple[bool, str]:
    prefix = ["sudo", "-n"] if sudo else []
    inner = ["/bin/sh", "-c", _drop_stmt(), "sh", "true"] if sudo else ["/bin/sh", "-c", "true"]
    argv = prefix + [b] + _static_flags("shared", sudo) + ["--setenv", "HOME", HOME_DIR, "--dir", HOME_DIR, "--chdir", "/usr"] + inner
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"bwrap probe failed: {e}"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or f"bwrap exited {p.returncode}").strip()[:300]
    return True, ""


def _detect() -> tuple[str | None, str]:
    b = shutil.which("bwrap")
    if not b:
        return None, "bubblewrap is not installed (sudo apt install bubblewrap)."
    ok, _ = _probe(b, sudo=True)
    if ok:
        return "sudo", "bwrap available (fresh /proc, commands run as your uid)"
    ok, why = _probe(b, sudo=False)
    if ok:
        return "user", "bwrap available (user namespaces)"
    return None, why or "bwrap is installed but its sandbox cannot start on this host."


def wrap(command: str, *, cwd: Path, workspace: Path, network: str = "shared") -> list[str]:
    """Build the bwrap argv that runs `command` (/bin/sh -c) inside the jail.

    `cwd` must be an absolute, resolved path: inside the workspace (mounted
    read-write) or anywhere else, in which case that one folder is mounted
    read-write too — reaching it already requires an explicit approval.
    """
    name, why = backend()
    if not name:
        raise FileNotFoundError(why)
    b = shutil.which("bwrap")
    if not b:
        raise FileNotFoundError("bwrap not found")
    ws = Path(workspace).resolve()
    cwd = Path(cwd).resolve()
    sudo = name == "sudo"
    path = "/usr/bin/sh" if Path("/usr/bin/sh").exists() else "/bin/sh"
    path_env = "/usr/local/bin:/usr/bin:/bin"
    if (ws / ".venv" / "bin").is_dir():
        path_env = f"{ws / '.venv' / 'bin'}:{path_env}"
    argv = (["sudo", "-n"] if sudo else []) + [b]
    argv += _static_flags(network, sudo)
    argv += ["--bind", str(ws), str(ws)]
    if not cwd.is_relative_to(ws):
        argv += ["--bind", str(cwd), str(cwd)]
    argv += ["--setenv", "PATH", path_env,
             "--setenv", "HOME", HOME_DIR, "--dir", HOME_DIR,
             "--chdir", str(cwd)]
    if sudo:
        argv += [path, "-c", _drop_stmt(), "sh", command]
    else:
        argv += [path, "-c", command]
    return argv
