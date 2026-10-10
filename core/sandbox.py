"""Run third-party plugin tools out of process (L1 containment).

Today a Python plugin's functions execute inside the service via exec_module,
with the service's environment, credentials and file access. This module runs
each tool call in a short-lived worker subprocess instead:

- scrubbed environment (no *KEY*/*TOKEN*/*SECRET*/*PASSWORD*/*CREDENTIAL*),
  plus an explicit PYTHONPATH so only the repo and stdlib resolve;
- working directory jailed to the Bot workspace;
- no credential store, no database, no engine objects cross the boundary —
  the worker gets the plugin config, the Bot identity and the serialised
  network-policy layers only;
- hard timeout with a full process-tree kill;
- plugin stdout is a single JSON line; anything else becomes an error result,
  never a crash in the service.

What this does and does not do: it kills entire bug classes (accidents,
ambient-secret leaks, runaway processes, a segfault taking down the service)
and it substantially raises the bar for malice. It is not a bulletproof
sandbox against a deliberately hostile plugin author — that needs OS-level
enforcement (job objects, low-integrity tokens: L2). Tools that genuinely need
live engine objects (browser pairing today: whatsapp) keep running in process
via the manifest's ``needs_engine`` flag.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRUB = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)

KEEP_ENV = frozenset({
    "SystemRoot", "windir", "ComSpec", "PATHEXT", "TMP", "TEMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "PATH", "LANG", "LC_ALL", "PYTHONUTF8", "PYTHONIOENCODING", "NUMBER_OF_PROCESSORS", "OS", "COMPUTERNAME",
})

# Plugin ids that need live engine objects (browser pairing etc.) and therefore
# run in process, exactly as before. Prefer the manifest's "needs_engine" flag;
# this is only a fallback so already-installed plugins keep working.
ENGINE_PLUGINS = frozenset({"whatsapp"})

DEFAULT_TIMEOUT = 180


def scrub_env(extra: dict | None = None) -> dict[str, str]:
    """A minimal environment for a worker: essentials kept, secrets dropped.

    Critical Windows keys are backfilled through os.environ.get as well, so a
    key the iteration misses (restricted launchers, odd shells) still survives
    when it really exists.
    """
    env = {k: v for k, v in os.environ.items() if k in KEEP_ENV and not SCRUB.search(k)}
    for k in ("SystemRoot", "windir", "COMSPEC"):
        v = os.environ.get(k)
        if v and k not in env and not SCRUB.search(k):
            env[k] = v
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONNOUSERSITE", "1")
    if extra:
        env.update(extra)
    return env


def should_isolate(manifest: dict) -> bool:
    """Secure by default: isolate unless the plugin opts out with needs_engine."""
    if manifest.get("needs_engine"):
        return False
    return manifest.get("id") not in ENGINE_PLUGINS


def resolve_interpreter() -> str | None:
    """A real Python interpreter for the worker, or None (frozen build without one)."""
    import shutil

    if getattr(sys, "frozen", False):
        for cand in ("python", "python3", "py"):
            found = shutil.which(cand)
            if found:
                return found
        return None
    return sys.executable


def run_tool(*, plugin_dir: str, tool: str, args: dict, bot: dict, thread_id: str, turn_id: str,
             config: dict, net_layers: list, workspace: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run one plugin tool in a worker process. Returns a ToolResult-shaped dict."""
    interp = resolve_interpreter()
    if interp is None:
        return {"text": "Plugin sandbox unavailable on this install (no Python runtime found).", "is_error": True}
    payload = {"dir": plugin_dir, "tool": tool, "args": args,
               "ctx": {"bot": bot, "thread_id": thread_id, "turn_id": turn_id},
               "config": config, "net_layers": net_layers}
    repo_root = Path(__file__).resolve().parent.parent
    env = scrub_env({"PYTHONPATH": str(repo_root)})
    try:
        proc = subprocess.Popen([interp, "-m", "core.sandbox_worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=workspace, env=env, text=True, encoding="utf-8",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return {"text": f"Could not start the plugin sandbox: {e}", "is_error": True}
    try:
        out, _err = proc.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return {"text": f"Plugin tool '{tool}' timed out after {timeout}s and was stopped.", "is_error": True}
    except Exception as e:  # noqa: BLE001
        _kill_tree(proc)
        return {"text": f"Plugin sandbox failed: {e}", "is_error": True}
    out = (out or "").strip()
    if not out:
        return {"text": f"Plugin tool '{tool}' produced no result (exit {proc.returncode}).", "is_error": True}
    try:
        data = json.loads(out)
    except ValueError:
        for line in reversed(out.splitlines()):
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
        else:
            return {"text": f"Plugin tool '{tool}' returned unreadable output.", "is_error": True}
    if not isinstance(data, dict) or "text" not in data:
        return {"text": f"Plugin tool '{tool}' returned a malformed result.", "is_error": True}
    data.setdefault("is_error", False)
    return data


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def tool_result_dict(text: str = "", data: str = "", images: list | None = None, is_error: bool = False,
                     url: str = "", path: str = "", untrusted: str = "") -> dict:
    return {"text": text, "data": data, "images": images or [], "is_error": is_error,
            "url": url, "path": path, "untrusted": untrusted}
