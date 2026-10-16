"""InstaBox cloud backend for disposable Linux sandboxes (see core/sandbox_linux.py).

Same contract as the local runner — ``run()`` returns {"exit", "output",
"timed_out"} — but the machine lives in InstaBox's cloud instead of this PC:
nothing runs locally, nothing persists locally, and the PC can be off.

Billing reality: every call creates a real VM (billed while alive, even idle),
so each run deletes its machine when done and sets a kill-on-timeout backstop.
API key: ``INSTABOX_API_KEY`` env (service env panel on hosts), never a password,
never in code. Exec calls cap at 600 s per InstaBox limits; longer jobs must be
split by the caller.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = "https://api.instabox.sh"
EXEC_LIMIT = 600


class InstaBoxError(RuntimeError):
    pass


def api_key() -> str | None:
    return os.environ.get("INSTABOX_API_KEY") or None


class Client:
    """Minimal InstaBox REST client (create/exec/delete + operation wait)."""

    def __init__(self, key: str, base_url: str = BASE_URL, timeout: int = 60):
        self.key = key
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": "Bearer " + self.key, "Accept": "application/json",
                "User-Agent": "zerothebot-sandbox", "Content-Type": "application/json",
                "Idempotency-Key": uuid.uuid4().hex}

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}")
                msg = (detail.get("error") or {}).get("message", str(detail)) if isinstance(detail, dict) else str(detail)
            except ValueError:
                msg = "HTTP %s" % e.code
            raise InstaBoxError(f"InstaBox {method} {path}: {msg}") from e
        except Exception as e:  # noqa: BLE001 (network down, DNS, TLS…)
            raise InstaBoxError(f"InstaBox unreachable: {e}") from e

    def wait_operation(self, op_id: str, timeout: int = 300) -> dict:
        t0 = time.time()
        while True:
            op = self.call("GET", "/v1/operations/" + op_id)
            state = op.get("state", "")
            if state in ("succeeded", "done"):
                return op
            if state in ("failed", "error") or op.get("error"):
                raise InstaBoxError("operation %s failed: %s" % (op_id, op.get("error")))
            if time.time() - t0 > timeout:
                raise InstaBoxError("operation %s did not finish in %ds" % (op_id, timeout))
            time.sleep(5)


def run(cmd: str, *, timeout: int = 120, key: str | None = None, template: str = "universal") -> dict:
    """Run a shell command on a disposable InstaBox VM. Never touches this PC.

    Creates the machine, runs one exec, deletes the machine. Returns
    {"exit", "output", "timed_out"} like the local runner.
    """
    key = key or api_key()
    if not key:
        return {"exit": -1, "output": "InstaBox is not configured: set INSTABOX_API_KEY in the service environment.", "timed_out": False}
    timeout = max(10, min(int(timeout or 120), EXEC_LIMIT))
    cli = Client(key, timeout=timeout + 90)
    machine_id = ""
    try:
        op = cli.call("POST", "/v1/machines", {"name": "zb-%s" % uuid.uuid4().hex[:8], "templateId": template,
                                               "timeoutMs": (timeout + 180) * 1000, "onTimeout": "kill",
                                               "metadata": {"owner": "zerothebot-run"}})
        op = cli.wait_operation(op["id"])
        machine_id = op.get("machineId", "")
        if not machine_id:
            return {"exit": -1, "output": "InstaBox did not return a machine.", "timed_out": False}
        try:
            res = cli.call("POST", "/v1/machines/%s/exec" % machine_id,
                           {"cmd": cmd, "timeoutSeconds": timeout})
        except InstaBoxError as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                return {"exit": -1, "output": "(TIMED OUT after %ds and was killed)" % timeout, "timed_out": True}
            raise
        out = (res.get("stdout") or "") + (res.get("stderr") or "")
        if len(out) > 20000:
            out = out[:10000] + "\n[... %d chars omitted ...]\n" % (len(out) - 20000) + out[-10000:]
        return {"exit": int(res.get("exitCode", -1)), "output": out or "(no output)", "timed_out": False}
    except InstaBoxError as e:
        return {"exit": -1, "output": str(e), "timed_out": False}
    finally:
        if machine_id:
            try:
                cli.call("DELETE", "/v1/machines/" + machine_id)
            except InstaBoxError:
                pass
