"""System routes split out of service/server.py: workspace files, backups, updates,
search/palette, command catalog, and the digest. All of them only depend on the engine,
so they live in one register() call to keep server.py focused on the agent API."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import Body, HTTPException, Request, Response

from core import backup as backup_mod
from core import commands
from core.engine import Engine
from core.files import FileError


def register(app: Any, eng: Engine, api: Any) -> None:
    """Mount the system routes. `api` is the shared auth dependency from create_app."""

    @app.get("/api/updates", dependencies=[api])
    def updates(refresh: bool = False) -> dict:
        return eng.updates.check(force=True) if refresh else eng.updates.state()

    @app.get("/api/backup", dependencies=[api])
    def backup_get(workspace: bool = False) -> Response:
        try:
            data = backup_mod.create(eng, include_workspace=workspace)
        except backup_mod.BackupError as e:
            raise HTTPException(400, str(e))
        name = "zerothebot-backup-" + time.strftime("%Y%m%d-%H%M") + ".zip"
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/backup/restore", dependencies=[api])
    async def backup_restore(request: Request) -> dict:
        data = await request.body()
        try:
            info = await asyncio.to_thread(backup_mod.stage_restore, data)
        except backup_mod.BackupError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "restart_required": True, **info}

    @app.get("/api/digest", dependencies=[api])
    def digest(spec: str = "today") -> dict:
        d = eng.digest.build(spec)
        return {**d, "headline": eng.digest.headline(d), "markdown": eng.digest.markdown(d)}

    @app.get("/api/ws/list", dependencies=[api])
    def ws_list(path: str = "") -> dict:
        try:
            return eng.files.list(path)
        except FileError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/ws/recent", dependencies=[api])
    def ws_recent(limit: int = 30) -> dict:
        return {"entries": eng.files.recent(max(1, min(200, limit))), "stats": eng.files.stats()}

    @app.get("/api/ws/search", dependencies=[api])
    def ws_search(q: str = "") -> dict:
        return {"entries": eng.files.search(q)}

    @app.get("/api/ws/preview", dependencies=[api])
    def ws_preview(path: str) -> dict:
        try:
            return eng.files.preview(path)
        except FileError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/ws/raw", dependencies=[api])
    def ws_raw(path: str) -> Response:
        try:
            p, mime = eng.files.raw(path)
        except FileError as e:
            raise HTTPException(400, str(e))
        return Response(p.read_bytes(), media_type=mime, headers={"Content-Disposition": f'attachment; filename="{p.name}"'})

    @app.delete("/api/ws/file", dependencies=[api])
    def ws_delete(path: str) -> dict:
        try:
            eng.files.delete(path)
        except FileError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.get("/api/search", dependencies=[api])
    def search(q: str = "", limit: int = 30) -> dict:
        return eng.search.run(q, max(1, min(100, limit)))

    @app.get("/api/palette", dependencies=[api])
    def palette(q: str = "", limit: int = 8) -> dict:
        """One search across Bots, group chats, messages, files and routines (the mobile search field)."""
        q = (q or "").strip()
        n = max(1, min(20, limit))
        out: dict[str, Any] = {"query": q, "bots": [], "groups": [], "messages": [], "files": [], "routines": [], "total": 0}
        if not q:
            return out
        low = q.lower()
        out["bots"] = [{"id": b["id"], "name": b["name"], "emoji": b["emoji"], "avatar": b.get("avatar", ""), "job": b.get("job", "")}
                       for b in eng.bots.list() if low in b["name"].lower() or low in (b.get("job") or "").lower()][:n]
        out["groups"] = [{"id": g["id"], "name": g.get("name") or "", "thread_id": g.get("thread_id") or ""}
                         for g in eng.messaging.list_groups() if low in (g.get("name") or "").lower()][:n]
        out["routines"] = [{"id": r["id"], "name": r.get("name") or "", "bot_id": r.get("bot_id") or "", "enabled": r.get("enabled")}
                           for r in eng.routines.list()
                           if low in ((r.get("name") or "") + " " + (r.get("prompt") or "") + " " + (r.get("skill") or "")).lower()][:n]
        try:
            out["files"] = (eng.files.search(q, n) or [])[:n]
        except Exception:  # noqa: BLE001 - search must never fail the palette
            out["files"] = []
        try:
            out["messages"] = (eng.search.run(q, n).get("messages") or [])[:n]
        except Exception:  # noqa: BLE001
            out["messages"] = []
        out["total"] = sum(len(out[k]) for k in ("bots", "groups", "messages", "files", "routines"))
        return out

    @app.get("/api/commands", dependencies=[api])
    def commands_list() -> list[dict]:
        return commands.listing()
