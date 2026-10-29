"""The Instabox machine this service runs on, as seen from the platform API.

The service already lives *inside* the VM, so the local filesystem stays the fast
path for files. This module is for the things only Instabox knows: the machine's
real state, CPU/RAM, console log, OOM kills, public ports, the desktop, activity
leases and webhook signatures.

Everything here is best-effort: a missing API key turns the health card into a
short "not configured" note instead of breaking Settings or chat.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import threading
import time
from typing import Any

import httpx

from . import secrets

API = (os.environ.get("INSTABOX_API_URL") or "https://api.instabox.sh").rstrip("/")
_ENV_MACHINE = os.environ.get("INSTABOX_MACHINE_ID", "").strip()

_lock = threading.RLock()
_cache: dict[str, tuple[float, Any]] = {}
_lease_lock = threading.Lock()
_last_lease = 0.0


class HostError(RuntimeError):
    """A platform call failed, or Instabox is not configured."""


# ------------------------------------------------------------------ plumbing
def api_key() -> str:
    return (secrets.get_secret("instabox:api_key") or os.environ.get("INSTABOX_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def machine_id() -> str:
    global _ENV_MACHINE
    if _ENV_MACHINE:
        return _ENV_MACHINE
    hit = _cache.get("machine_id")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    machines = _request("GET", "/v1/machines").get("machines") or []
    if not machines:
        raise HostError("No Instabox machine is visible to this API key.")
    # Prefer the one that looks like this host; otherwise the only one.
    mid = machines[0]["id"]
    if len(machines) > 1:
        for m in machines:
            if (m.get("name") or "").lower() in ("zerothebot", "zbot"):
                mid = m["id"]
                break
    _ENV_MACHINE = mid
    with _lock:
        _cache["machine_id"] = (time.time(), mid)
    return mid


def _request(method: str, path: str, *, body: Any = None, raw: bool = False,
             timeout: float = 20.0, extra_headers: dict[str, str] | None = None):
    key = api_key()
    if not key:
        raise HostError("Instabox is not connected: set ZEROTHEBOT_SECRET_INSTABOX_API_KEY on the server.")
    headers = {"Authorization": "Bearer " + key, "Accept": "*/*" if raw else "application/json"}
    if body is not None and not raw:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    try:
        r = httpx.request(method, API + path, headers=headers,
                          content=(json.dumps(body).encode() if body is not None and not raw else body),
                          timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise HostError(f"Instabox is unreachable: {e}") from e
    if r.status_code >= 400:
        msg = ""
        try:
            msg = (r.json().get("error") or {}).get("message") or ""
        except Exception:  # noqa: BLE001
            msg = (r.text or "")[:200]
        raise HostError(f"Instabox {method} {path} -> {r.status_code}: {msg or 'request failed'}")
    if raw:
        return r.content
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


def _get(path: str, ttl: float = 8.0, timeout: float = 20.0):
    with _lock:
        hit = _cache.get(path)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = _request("GET", path, timeout=timeout)
    with _lock:
        _cache[path] = (time.time(), val)
    return val


def _post(path: str, body: Any = None, *, timeout: float = 40.0):
    return _request("POST", path, body=body, timeout=timeout,
                    extra_headers={"Idempotency-Key": os.urandom(16).hex()})


# ------------------------------------------------------------------- reading
def machine() -> dict:
    return _get(f"/v1/machines/{machine_id()}", ttl=5.0)


def public_url() -> str:
    """The HTTPS origin the phone reaches this machine on (port 8765)."""
    env = (os.environ.get("ZEROTHEBOT_PUBLIC_URL") or os.environ.get("ZB_PUBLIC_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    return f"https://8765-{machine_id().removeprefix('m_')}.instabox.tech"


def ports() -> list[dict]:
    try:
        return _get(f"/v1/machines/{machine_id()}/ports", ttl=60.0).get("ports") or []
    except HostError:
        return []


def metrics() -> list[dict]:
    try:
        return _get(f"/v1/machines/{machine_id()}/metrics", ttl=25.0).get("metrics") or []
    except HostError:
        return []


def processes(limit: int = 80) -> dict:
    try:
        return _get(f"/v1/machines/{machine_id()}/processes", ttl=10.0)
    except HostError:
        return {}


def logs(tail: int = 40) -> dict:
    try:
        return _get(f"/v1/machines/{machine_id()}/logs?tail={max(1, min(200, tail))}", ttl=20.0)
    except HostError:
        return {}


def history(limit: int = 20) -> list[dict]:
    try:
        return _get(f"/v1/machines/{machine_id()}/history?limit={max(1, min(100, limit))}", ttl=15.0).get("commands") or []
    except HostError:
        return []


def _local_disk() -> dict:
    for p in ("/data", "/"):
        try:
            u = shutil.disk_usage(p)
            return {"path": p, "used": u.used, "total": u.total, "pct": round(u.used / u.total * 100, 1) if u.total else 0}
        except OSError:
            continue
    return {}


def _local_load() -> dict:
    out: dict[str, Any] = {}
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        out["load"] = []
    try:
        out["uptime_seconds"] = int(float(open("/proc/uptime").read().split()[0]))
    except Exception:  # noqa: BLE001
        out["uptime_seconds"] = 0
    return out


def health(refresh: bool = False) -> dict:
    """One compact snapshot for Settings: platform state + live load + why it died."""
    if refresh:
        with _lock:
            _cache.clear()
    if not configured():
        return {"configured": False, "ok": False,
                "hint": "Add ZEROTHEBOT_SECRET_INSTABOX_API_KEY on the server to see machine health."}
    out: dict[str, Any] = {"configured": True, "ok": False, "checked_at": time.time()}
    try:
        m = machine()
        spec = m.get("spec") or {}
        out["machine"] = {
            "id": m.get("id"), "name": m.get("name"), "state": m.get("state"),
            "vcpus": spec.get("vcpus"), "mem_mib": spec.get("memMiB"),
            "disk_gib": spec.get("persistentDiskGiB"),
            "template": (m.get("template") or {}).get("id"),
            "template_version": (m.get("template") or {}).get("version"),
            "auto_suspend_seconds": m.get("autoSuspendAfterSeconds"),
            "oom_kills": m.get("oomKills") or 0,
            "last_oom": m.get("lastOom"),
            "storage_synced": (m.get("storage") or {}).get("synced"),
        }
        out["ok"] = True
    except HostError as e:
        out["error"] = str(e)

    series = metrics()
    if series:
        last = series[-1]
        out["cpu"] = {"pct": last.get("cpuUsedPct"), "count": last.get("cpuCount")}
        mem_u, mem_t = last.get("memUsed") or 0, last.get("memTotal") or 0
        out["memory"] = {"used": mem_u, "total": mem_t, "pct": round(mem_u / mem_t * 100, 1) if mem_t else 0}
        out["net"] = {"in": last.get("netInBytesPerSec"), "out": last.get("netOutBytesPerSec")}
        out["series"] = [{"t": s.get("timestamp"), "cpu": s.get("cpuUsedPct"),
                          "mem": (s.get("memUsed") or 0)} for s in series[-24:]]
    procs = processes()
    if procs:
        ps = procs.get("processes") or []
        ps = sorted(ps, key=lambda p: (p.get("memPercent") or 0, p.get("cpuPercent") or 0), reverse=True)
        out["processes"] = {
            "count": len(ps),
            "top": [{"pid": p.get("pid"), "cpu": p.get("cpuPercent"), "mem": p.get("memPercent"),
                     "command": (p.get("command") or "")[:90]} for p in ps[:5]],
        }
    lg = logs(30)
    if lg:
        out["logs"] = [ln for ln in (lg.get("console") or "").splitlines() if ln][-24:]
        out["operations"] = [{"type": o.get("type"), "state": o.get("state"), "trigger": o.get("trigger"),
                              "at": o.get("updatedAt"), "error": o.get("error")}
                             for o in (lg.get("operations") or [])[-6:]]
    out["ports"] = [{"port": p.get("port"), "url": p.get("url"), "name": p.get("name"),
                     "access": p.get("access")} for p in ports()]
    out["disk"] = _local_disk()
    out.update(_local_load())
    out["recent"] = [{"command": (c.get("command") or "")[:90], "at": c.get("startedAt"),
                      "status": c.get("status"), "exit": c.get("exitCode")} for c in history(8)]
    return out


# -------------------------------------------------------------------- acting
def wake() -> dict:
    """Resume an idle-paused machine or start a stopped one."""
    m = machine()
    state = m.get("state")
    if state == "running":
        return {"state": state, "woke": False}
    if state in ("suspended", "paused", "suspending"):
        _post(f"/v1/machines/{machine_id()}/resume")
        return {"state": "resuming", "woke": True}
    if state in ("stopped", "stopping"):
        _post(f"/v1/machines/{machine_id()}/start")
        return {"state": "starting", "woke": True}
    if state in ("starting", "resuming"):
        return {"state": state, "woke": False}
    _post(f"/v1/machines/{machine_id()}/resume")
    return {"state": state, "woke": True}


def lease(ttl: int = 900) -> dict:
    """Hold the machine awake while a Bot turn or a routine is running."""
    ttl = max(60, min(3600, int(ttl)))
    _request("PUT", f"/v1/machines/{machine_id()}/activity-leases/zerothebot-service",
             body={"ttlSeconds": ttl}, timeout=15.0)
    return {"ok": True, "ttl": ttl}


def lease_soon(ttl: int = 900) -> None:
    """Fire-and-forget lease renewal, at most once every five minutes."""
    global _last_lease
    if not configured():
        return
    with _lease_lock:
        if time.time() - _last_lease < 300:
            return
        _last_lease = time.time()

    def run():
        try:
            lease(ttl)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------- desktop
def desktop_start() -> dict:
    return _post(f"/v1/machines/{machine_id()}/desktop",
                 {"action": "start", "width": 1280, "height": 800}, timeout=60.0)


def desktop_link(view_only: bool = True, ttl: int = 3600) -> dict:
    ttl = max(60, min(86400, int(ttl)))
    return _post(f"/v1/machines/{machine_id()}/desktop/stream",
                 {"viewOnly": bool(view_only), "ttlSeconds": ttl}, timeout=40.0)


def desktop_revoke() -> dict:
    _request("DELETE", f"/v1/machines/{machine_id()}/desktop/stream", timeout=20.0)
    return {"ok": True}


# --------------------------------------------------------------------- files
def _q(path: str, **extra) -> str:
    return str(httpx.QueryParams({"path": path, **extra}))


def list_files(path: str = "/data/home") -> dict:
    return _get(f"/v1/machines/{machine_id()}/files?{_q(path)}", ttl=2.0)


def read_bytes(path: str) -> bytes:
    return _request("GET", f"/v1/machines/{machine_id()}/files/content?{_q(path)}",
                    raw=True, timeout=120.0)


def write_bytes(path: str, data: bytes, parents: bool = True) -> dict:
    return _request("PUT", f"/v1/machines/{machine_id()}/files?{_q(path, parents='true' if parents else 'false')}",
                    body=data, raw=True, timeout=300.0)


def upload_url(path: str, expires: int = 900) -> dict:
    return _post(f"/v1/machines/{machine_id()}/files/upload-url",
                 {"path": path, "parents": True, "expiresInSeconds": max(60, min(3600, int(expires)))},
                 timeout=30.0)


# ------------------------------------------------------------------ webhooks
def webhook_secret() -> str:
    return (secrets.get_secret("instabox:webhook") or "").strip()


def verify_webhook(headers, raw: bytes) -> dict:
    """Standard Webhooks signature check, as Instabox documents it."""
    secret = webhook_secret()
    if not secret:
        raise HostError("No Instabox webhook secret is stored on this server.")
    wid = headers.get("webhook-id") or ""
    ts = headers.get("webhook-timestamp") or ""
    sig = headers.get("webhook-signature") or ""
    if not wid or not ts or not sig:
        raise HostError("Missing webhook signature headers.")
    try:
        when = int(ts)
    except ValueError as e:
        raise HostError("Bad webhook timestamp.") from e
    if abs(time.time() - when) > 300:
        raise HostError("Webhook timestamp is too old.")
    raw_key = secret[6:] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw_key)
    except Exception as e:  # noqa: BLE001
        raise HostError("Webhook secret is not valid base64.") from e
    want = hmac.new(key, f"{wid}.{ts}.".encode() + raw, hashlib.sha256).digest()
    for part in sig.split():
        if not part.startswith("v1,"):
            continue
        try:
            got = base64.b64decode(part[3:])
        except Exception:  # noqa: BLE001
            continue
        if hmac.compare_digest(got, want):
            return json.loads(raw.decode("utf-8"))
    raise HostError("Webhook signature did not match.")


def register_webhook(url: str, events: list[str] | None = None) -> dict:
    """Create (or find) the org webhook and store its signing secret."""
    orgs = _get("/v1/orgs").get("orgs") or []
    if not orgs:
        raise HostError("No Instabox org is visible to this API key.")
    org = orgs[0]["id"]
    events = events or ["sandbox.started", "sandbox.suspended", "sandbox.stopped",
                        "sandbox.error", "sandbox.deleted"]
    listed = _get(f"/v1/orgs/{org}/webhooks")
    for w in (listed.get("webhooks") or listed.get("items") or []):
        if (w.get("url") or "") == url:
            return {"ok": True, "existing": True, "id": w.get("id"), "url": url}
    created = _post(f"/v1/orgs/{org}/webhooks", {"url": url, "events": events})
    secret = created.get("secret") or created.get("signingSecret") or created.get("signing_secret")
    if secret:
        try:
            secrets.set_secret("instabox:webhook", secret)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "existing": False, "id": created.get("id"), "url": url,
            "secret_saved": bool(secret), "events": events}
