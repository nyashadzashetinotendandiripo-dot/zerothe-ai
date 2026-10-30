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
import re
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


# ---------------------------------------------------------------- invalidating
def _forget(*prefixes: str) -> None:
    """Drop cached reads so a write is visible on the next GET."""
    with _lock:
        for k in list(_cache):
            if any(k.startswith(p) for p in prefixes):
                _cache.pop(k, None)


# ------------------------------------------------------------------ snapshots
def snapshots() -> list[dict]:
    rows = _get("/v1/snapshots", ttl=5.0).get("snapshots") or []
    return [{"id": s.get("id"), "name": s.get("name"), "status": s.get("status"),
             "created_at": s.get("createdAt"), "size": (s.get("sizeBytes") or s.get("dataSizeBytes")),
             "root_only": s.get("rootOnly"), "template": s.get("template"),
             "source": s.get("sourceMachineId")} for s in rows]


def snapshot_create(name: str, description: str = "", root_only: bool = False,
                    wait: int = 45) -> dict:
    """Save a snapshot. A live save answers 202 and finishes in the background."""
    name = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")[:63]
    if not name:
        name = "zb-" + time.strftime("%Y%m%d-%H%M%S")
    if not re.match(r"^[a-z0-9]", name):
        name = "zb-" + name
    out = _post(f"/v1/machines/{machine_id()}/snapshots",
                {"name": name, "description": (description or "")[:200], "rootOnly": bool(root_only)},
                timeout=120.0)
    sid = out.get("id")
    deadline = time.time() + max(0, wait)
    while sid and out.get("status") in ("pending", "saving") and time.time() < deadline:
        time.sleep(2.0)
        try:
            out = _get(f"/v1/snapshots/{sid}", ttl=0.0)
        except HostError:
            break
    _forget("/v1/snapshots")
    return out


def snapshot_delete(sid: str) -> dict:
    _request("DELETE", f"/v1/snapshots/{sid}", timeout=60.0,
             extra_headers={"X-Confirm-Delete": sid})
    _forget("/v1/snapshots")
    return {"ok": True}


def restore(sid: str, desired_state: str = "running") -> dict:
    """Put the machine's disks back to a snapshot, in place (keeps its URLs)."""
    if not sid:
        raise HostError("Pick a snapshot to restore.")
    out = _post(f"/v1/machines/{machine_id()}/restore",
                {"snapshotId": sid, "desiredState": desired_state}, timeout=120.0)
    _forget("/v1/machines/", "/v1/snapshots")
    return out


def fork(name: str = "") -> dict:
    """A copy of this machine on the same host (no snapshot, no downtime)."""
    body: dict[str, Any] = {}
    if name:
        body["name"] = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:63]
    out = _post(f"/v1/machines/{machine_id()}/fork", body or None, timeout=180.0)
    _forget("/v1/machines")
    return out


# ---------------------------------------------------------------------- ports
def port_publish(port: int, name: str = "", access: str = "public") -> dict:
    port = int(port)
    if not 1 <= port <= 65535:
        raise HostError("Port must be between 1 and 65535.")
    body: dict[str, Any] = {"port": port, "access": "private" if access == "private" else "public"}
    if name:
        body["name"] = name[:60]
    out = _post(f"/v1/machines/{machine_id()}/ports", body, timeout=60.0)
    _forget("/v1/machines/", "/v1/ports")
    return out


def port_unpublish(port: int) -> dict:
    _request("DELETE", f"/v1/machines/{machine_id()}/ports/{int(port)}", timeout=45.0)
    _forget("/v1/machines/", "/v1/ports")
    return {"ok": True}


# -------------------------------------------------------------------- secrets
def org_id() -> str:
    hit = _cache.get("org_id")
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    orgs = _get("/v1/orgs", ttl=60.0).get("orgs") or []
    if not orgs:
        raise HostError("No Instabox org is visible to this API key.")
    org = orgs[0]["id"]
    with _lock:
        _cache["org_id"] = (time.time(), org)
    return org


def org_secrets() -> dict:
    """Proxy/env Secrets in the org. Values are never returned by the API."""
    try:
        items = _get(f"/v1/orgs/{org_id()}/secrets", ttl=20.0).get("items") or []
    except HostError as e:
        return {"items": [], "error": str(e)}
    return {"items": [{"name": s.get("name"), "delivery": s.get("delivery"),
                       "hosts": s.get("hosts") or [], "version": s.get("version"),
                       "size": s.get("size"), "updated_at": s.get("updatedAt")} for s in items]}


def secret_set(name: str, value: str, delivery: str = "proxy",
               hosts: list[str] | None = None) -> dict:
    name = (name or "").strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", name):
        raise HostError("Secret names use letters, digits and _, starting with a letter or _.")
    if not value:
        raise HostError("A secret needs a value.")
    delivery = "env" if delivery == "env" else "proxy"
    body: dict[str, Any] = {"value": value, "delivery": delivery}
    if delivery == "proxy":
        clean = [h.strip().lower() for h in (hosts or []) if h and h.strip()]
        body["hosts"] = clean or ["*"]
    try:
        out = _request("PUT", f"/v1/orgs/{org_id()}/secrets/{name}", body=body, timeout=45.0)
    except HostError as e:
        if "missing_scope" in str(e) or "403" in str(e):
            raise HostError(
                "Writing Instabox Secrets needs a workspace-admin API key; this server's key "
                "belongs to a personal org. Save the key in ZerotheBot's own encrypted vault instead."
            ) from e
        raise
    _forget("/v1/orgs/", "/v1/machines/")
    return out


def secret_delete(name: str) -> dict:
    _request("DELETE", f"/v1/orgs/{org_id()}/secrets/{name}", timeout=45.0,
             extra_headers={"X-Confirm-Delete": name})
    _forget("/v1/orgs/", "/v1/machines/")
    return {"ok": True}


def machine_secrets() -> dict:
    try:
        return _get(f"/v1/machines/{machine_id()}/secrets", ttl=10.0)
    except HostError as e:
        return {"error": str(e)}


def machine_secrets_select(secrets_all: bool | None = None,
                           env: list[str] | None = None) -> dict:
    body: dict[str, Any] = {}
    if secrets_all is not None:
        body["secrets"] = "all" if secrets_all else []
    if env is not None:
        body["envSecrets"] = env
    if not body:
        raise HostError("Nothing to change.")
    out = _request("PATCH", f"/v1/machines/{machine_id()}/secrets", body=body, timeout=45.0)
    _forget("/v1/machines/")
    return out


# ----------------------------------------------------------------------- exec
def exec_run(cmd: str, cwd: str | None = None, timeout: int = 60) -> dict:
    body: dict[str, Any] = {"cmd": cmd, "timeoutSeconds": max(5, min(600, int(timeout)))}
    if cwd:
        body["cwd"] = cwd
    return _post(f"/v1/machines/{machine_id()}/exec", body, timeout=float(body["timeoutSeconds"] + 20))


def exec_stream(cmd: str, cwd: str | None = None, timeout: int = 120):
    """Yield {event, data} as the command writes output (SSE from Instabox)."""
    key = api_key()
    if not key:
        raise HostError("Instabox is not connected on this server.")
    body: dict[str, Any] = {"cmd": cmd, "timeoutSeconds": max(5, min(600, int(timeout)))}
    if cwd:
        body["cwd"] = cwd
    headers = {"Authorization": "Bearer " + key, "Accept": "text/event-stream",
               "Content-Type": "application/json"}
    try:
        with httpx.stream("POST", f"{API}/v1/machines/{machine_id()}/exec",
                          headers=headers, content=json.dumps(body),
                          timeout=float(body["timeoutSeconds"] + 30)) as r:
            if r.status_code >= 400:
                r.read()
                raise HostError(f"Instabox exec -> {r.status_code}: {(r.text or '')[:200]}")
            event = "message"
            for line in r.iter_lines():
                line = line or ""
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        data = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        data = {"data": raw}
                    yield {"event": event, "data": data}
    except HostError:
        raise
    except Exception as e:  # noqa: BLE001
        raise HostError(f"Instabox exec failed: {e}") from e


def exec_history(limit: int = 20) -> list[dict]:
    rows = _get(f"/v1/machines/{machine_id()}/history?limit={max(1, min(50, limit))}",
                ttl=5.0).get("commands") or []
    return [{"id": c.get("id"), "command": (c.get("command") or "")[:400],
             "at": c.get("startedAt"), "ms": c.get("durationMs"),
             "status": c.get("status"), "exit": c.get("exitCode")} for c in rows]


def exec_get(exec_id: str) -> dict:
    return _get(f"/v1/machines/{machine_id()}/history/{exec_id}", ttl=3600.0)


# ------------------------------------------------------------------- template
def template_info() -> dict:
    m = machine()
    cur = m.get("template") or {}
    tpls = _get("/v1/templates", ttl=300.0).get("templates") or []
    same = [t for t in tpls if t.get("id") == cur.get("id") and not t.get("hidden")]

    def ver(t):
        return str(t.get("version") or "")

    latest = max(same, key=ver) if same else None
    storage = (m.get("storage") or {})
    return {
        "current": {"id": cur.get("id"), "version": cur.get("version")},
        "latest": {"id": latest.get("id"), "version": latest.get("version"),
                   "description": (latest.get("description") or "")[:240]} if latest else None,
        "up_to_date": bool(latest) and ver(latest) == str(cur.get("version") or ""),
        "data_bytes": storage.get("diskBytes") or storage.get("sizeBytes") or _local_disk().get("total"),
        "machine": {"id": m.get("id"), "name": m.get("name")},
        "refresh_allowed": os.environ.get("ZEROTHEBOT_ALLOW_TEMPLATE_REFRESH") == "1",
    }


def template_plan() -> dict:
    """What moving to the newest template would cost, without doing it."""
    info = template_info()
    if info["up_to_date"]:
        return {**info, "steps": [], "warnings": ["This machine already runs the newest template."]}
    steps = [
        "Save a snapshot (rollback point).",
        "Create a new machine from the newest template (new machine ID).",
        "Copy /data across (repos, venv, threads DB, OTA bundles).",
        "Repoint the phone and OTA URLs at the new machine.",
        "Keep the old machine stopped until the new one answers.",
    ]
    warnings = [
        "A template upgrade cannot happen in place: Instabox has no upgrade endpoint, and a "
        "machine created from a snapshot keeps the snapshot's template.",
        "The new machine gets a new ID, so its public port URLs change; the app and OTA "
        "manifest URL must be updated (that needs a new APK build).",
        "Copying /data is limited by the VM's outbound bandwidth (~6 MB/s).",
    ]
    return {**info, "steps": steps, "warnings": warnings}


def template_refresh(confirm: str, name: str = "") -> dict:
    """Stage an upgrade: snapshot now, create a stopped machine on the new template.

    Deliberately does not cut over: that would change the machine ID and every
    public URL. The caller gets the new machine's ID and the remaining steps.
    """
    if os.environ.get("ZEROTHEBOT_ALLOW_TEMPLATE_REFRESH") != "1":
        raise HostError("Template refresh is disabled on this server "
                        "(set ZEROTHEBOT_ALLOW_TEMPLATE_REFRESH=1 to allow it).")
    info = template_info()
    if info["up_to_date"]:
        return {"ok": True, "up_to_date": True}
    m = info["machine"]
    if (confirm or "").strip().lower() != (m.get("name") or "").lower():
        raise HostError(f"Type the machine name ({m.get('name')}) to confirm.")
    snap = snapshot_create("zb-pre-upgrade-" + time.strftime("%Y%m%d-%H%M%S"),
                           "Before template refresh", wait=0)
    new_name = re.sub(r"[^a-z0-9-]+", "-", (name or (m.get("name") or "zb") + "-next").lower()).strip("-")[:63]
    created = _post("/v1/machines", {
        "templateId": info["latest"]["id"],
        "templateVersion": info["latest"]["version"],
        "name": new_name,
        "desiredState": "stopped",
        "orgId": org_id(),
    }, timeout=180.0)
    _forget("/v1/machines")
    return {"ok": True, "snapshot": {"id": snap.get("id"), "status": snap.get("status")},
            "machine": created.get("machineId") or created.get("id"), "name": new_name,
            "template": info["latest"], "steps": template_plan()["steps"][2:]}
