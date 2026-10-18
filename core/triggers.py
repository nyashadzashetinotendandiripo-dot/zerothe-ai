"""Event triggers: inbound webhooks that wake a Bot, in the style of Slack or GitHub.

Each trigger holds a secret token. POSTing any JSON payload to /api/hooks/<token> opens a
fresh thread for the Bot and runs a turn built from the trigger's prompt plus the payload,
so an external event (a form, a CI job, a monitoring alert) starts real work unattended.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .db import new_id, now

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine


class TriggerError(ValueError):
    pass


class Triggers:
    def __init__(self, engine: "Engine"):
        self.engine = engine
        self.db = engine.db

    # -- views -------------------------------------------------------------------
    def _out(self, t: dict) -> dict:
        t = dict(t)
        bot = self.engine.bots.get(t["bot_id"])
        t["bot_name"] = bot["name"] if bot else "(deleted)"
        t["enabled"] = bool(t.get("enabled"))
        t["url"] = f"/api/hooks/{t['token']}"
        return t

    def list(self, bot_id: str | None = None) -> list[dict]:
        rows = self.db.query("SELECT * FROM triggers" + (" WHERE bot_id=?" if bot_id else "") + " ORDER BY created_at",
                             (bot_id,) if bot_id else ())
        return [self._out(r) for r in rows]

    def get(self, tid: str) -> dict | None:
        r = self.db.one("SELECT * FROM triggers WHERE id=?", (tid,))
        return self._out(r) if r else None

    # -- CRUD --------------------------------------------------------------------
    def create(self, bot_id: str, name: str, prompt: str = "", enabled: bool = True) -> dict:
        if not self.engine.bots.get(bot_id):
            raise TriggerError("No such Bot.")
        tid = new_id()
        self.db.insert("triggers", {"id": tid, "bot_id": bot_id, "name": (name or "Trigger").strip()[:80] or "Trigger",
                                    "token": secrets.token_urlsafe(24), "prompt": str(prompt or "").strip()[:2000],
                                    "enabled": int(bool(enabled)), "fire_count": 0, "created_at": now(), "last_fired_at": 0})
        self.engine.events.publish("triggers", change="created", trigger_id=tid)
        return self.get(tid)  # type: ignore

    def update(self, tid: str, **f: Any) -> dict:
        cur = self.db.one("SELECT * FROM triggers WHERE id=?", (tid,))
        if not cur:
            raise TriggerError("No such trigger.")
        patch: dict[str, Any] = {}
        if "name" in f and f["name"] is not None:
            patch["name"] = str(f["name"]).strip()[:80] or "Trigger"
        if "prompt" in f and f["prompt"] is not None:
            patch["prompt"] = str(f["prompt"]).strip()[:2000]
        if "enabled" in f and f["enabled"] is not None:
            patch["enabled"] = int(bool(f["enabled"]))
        if "bot_id" in f and f["bot_id"] is not None:
            if not self.engine.bots.get(f["bot_id"]):
                raise TriggerError("No such Bot.")
            patch["bot_id"] = f["bot_id"]
        if patch:
            self.db.update("triggers", tid, patch)
            self.engine.events.publish("triggers", change="updated", trigger_id=tid)
        return self.get(tid)  # type: ignore

    def rotate(self, tid: str) -> dict:
        if not self.db.one("SELECT id FROM triggers WHERE id=?", (tid,)):
            raise TriggerError("No such trigger.")
        self.db.update("triggers", tid, {"token": secrets.token_urlsafe(24)})
        self.engine.events.publish("triggers", change="updated", trigger_id=tid)
        return self.get(tid)  # type: ignore

    def delete(self, tid: str) -> None:
        self.db.execute("DELETE FROM triggers WHERE id=?", (tid,))
        self.engine.events.publish("triggers", change="deleted", trigger_id=tid)

    # -- firing ------------------------------------------------------------------
    def fire(self, token: str, payload: dict | None = None) -> dict:
        r = self.db.one("SELECT * FROM triggers WHERE token=?", (token,))
        if not r or not r["enabled"]:
            raise TriggerError("Unknown or disabled trigger.")
        threading.Thread(target=self._run, args=[r["id"], payload or {}], daemon=True, name=f"trigger-{r['id']}").start()
        return {"ok": True, "trigger_id": r["id"], "bot_id": r["bot_id"]}

    def _run(self, tid: str, payload: dict) -> None:
        r = self.db.one("SELECT * FROM triggers WHERE id=?", (tid,))
        if not r:
            return
        bot = self.engine.bots.get(r["bot_id"])
        try:
            if not bot or bot["paused"] or bot["archived"]:
                return
            if self.engine.usage.over_limit() or self.engine.usage.bot_over_budget(bot):
                return
            th = self.engine.threads.create(bot["id"], f"{r['name']} · {datetime.now().strftime('%b %d %H:%M')}", kind="trigger")
            body = json.dumps(payload, ensure_ascii=False, default=str)[:4000] if payload else "(no payload)"
            task = (f"[Event trigger: {r['name']}] An external event just fired this trigger. Handle it end to end and keep "
                    f"the report short and concrete.\n\n{r['prompt']}\n\nEvent payload:\n{body}").strip()
            self.engine.turns.run_sync(bot["id"], th["id"], task, trigger="event")
            self.engine.notify("trigger", bot, th["id"], f"Event: {r['name']}", "Handled an incoming event.")
        except Exception:  # noqa: BLE001 - a bad trigger must never kill the service
            pass
        finally:
            self.db.update("triggers", tid, {"last_fired_at": now(), "fire_count": int(r["fire_count"] or 0) + 1})
