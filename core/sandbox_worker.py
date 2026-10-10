"""Worker side of the plugin sandbox (core/sandbox.py).

Launched as ``python -m core.sandbox_worker`` with a single JSON payload on
stdin. It re-imports the plugin entry point by path inside this throwaway
process — never the service — calls one tool with a minimal context, and prints
exactly one JSON line (a ToolResult-shaped dict) to stdout.

The worker deliberately has no engine, database, credential store or ambient
secrets: config values and the serialised network-policy layers arrive in the
payload, and HTTP goes through the same policy check as in-process calls.
"""
from __future__ import annotations

import inspect
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace


def _fail(text: str) -> int:
    print(json.dumps({"text": text, "is_error": True}), flush=True)
    return 0


def _check_layers(layers: list, url: str) -> tuple[bool, str]:
    """Same rules as NetworkPolicy.check, over serialised layer dicts."""
    from urllib.parse import urlparse

    from core.netpolicy import _is_private, _match, host_of

    host = host_of(url)
    if not host:
        return False, "Invalid URL."
    if urlparse(url if "://" in url else "http://" + url).scheme not in ("http", "https"):
        return False, "Only http/https URLs are allowed."
    for layer in layers:
        if layer.get("private") and _is_private(host):
            return False, f"{host} is a local/private address and is blocked by {layer.get('name', 'policy')}."
        if any(_match(host, d) for d in layer.get("deny", [])):
            return False, f"{host} is on the deny list of {layer.get('name', 'policy')}."
        if layer.get("mode") == "allowlist" and not any(_match(host, a) for a in layer.get("allow", [])):
            return False, f"{host} is not on the allow list of {layer.get('name', 'policy')}."
    return True, ""


class _WorkerAPI:
    """The sliver of PluginAPI a sandboxed tool gets: config + policy-checked HTTP."""

    def __init__(self, config: dict, net_layers: list, plugin_name: str):
        self._config = config or {}
        self._layers = net_layers or []
        self._name = plugin_name

    def config(self, key: str, default: str = "") -> str:
        v = self._config.get(key, default)
        return v if isinstance(v, str) else ("" if v is None else str(v))

    def http(self, ctx, method: str, url: str, **kw: object) -> object:
        import urllib.error
        import urllib.parse
        import urllib.request

        ok, why = _check_layers(self._layers, url)
        if not ok:
            raise RuntimeError(f"Blocked by network policy: {why}")
        headers = dict(kw.get("headers") or {})
        params = kw.get("params") or {}
        full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params) if params else url
        body = kw.get("json_body")
        data = json.dumps(body).encode() if body is not None else kw.get("data")
        if isinstance(data, str):
            data = data.encode()
        req = urllib.request.Request(full, data=data, method=str(method).upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=float(kw.get("timeout", 30) or 30)) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(f"{self._name} refused the request (HTTP {e.code}). Check the credentials in Plugins.")
            if e.code == 429:
                raise RuntimeError(f"{self._name} rate-limited the request. Wait and retry.")
            raise RuntimeError(f"{self._name} error {e.code}.")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Could not reach {self._name}: {e}") from e
        if status >= 400:
            raise RuntimeError(f"{self._name} error {status}.")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", "replace")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return _fail("Sandbox worker got an unreadable payload.")
    plugdir = Path(str(payload.get("dir", "")))
    entry = plugdir / "plugin.py"
    manifest_p = plugdir / "plugin.json"
    try:
        manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.exists() else {}
    except ValueError:
        manifest = {}
    tool_name = str(payload.get("tool", ""))
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        return _fail("Sandbox worker got malformed arguments.")
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("sandbox_plugin", entry)
        if not spec or not spec.loader:
            return _fail("Sandbox worker could not load the plugin entry point.")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        register = getattr(mod, "register", None)
        if not callable(register):
            return _fail("Plugin has no register(api) entry point.")
        api = _WorkerAPI(payload.get("config") or {}, payload.get("net_layers") or [], str(manifest.get("name", plugdir.name)))
        tools = register(api)
        fn = None
        for t in tools or []:
            if isinstance(t, dict) and t.get("name") == tool_name:
                fn = t.get("handler")
                break
        if not callable(fn):
            return _fail(f"Plugin has no tool named '{tool_name}'.")
        ctx = SimpleNamespace(bot=(payload.get("ctx") or {}).get("bot", {}),
                              thread_id=(payload.get("ctx") or {}).get("thread_id", ""),
                              turn_id=(payload.get("ctx") or {}).get("turn_id", ""))
        n_params = len(inspect.signature(fn).parameters)
        out = fn(ctx, args) if n_params >= 2 else fn(args)
    except Exception:  # noqa: BLE001 (a crashing plugin must not take down the service)
        tail = (traceback.format_exc(limit=3) or "").strip().splitlines()
        return _fail("Plugin tool crashed: " + (tail[-1] if tail else "unknown error"))
    if out is None:
        out = ""
    if hasattr(out, "__dataclass_fields__"):
        import dataclasses

        out = dataclasses.asdict(out)
    if isinstance(out, dict):
        out.setdefault("is_error", False)
        print(json.dumps(out, default=str), flush=True)
        return 0
    print(json.dumps({"text": out if isinstance(out, str) else str(out)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
