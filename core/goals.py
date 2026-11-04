"""Goals: long-running missions a Bot works toward over hours or days.

A goal is a title plus an optional "done when" description, pinned to one Bot's
main thread. The engine prods the Bot on a cadence: the Bot looks at the real
current state, does the next useful step (within its approval mode), and posts a
one-line progress update. When a reply contains the GOAL_COMPLETE marker the
goal is marked done automatically. Nothing here runs unbounded: each prod is one
ordinary turn, bounded by the Bot's step limit and approval mode like any other.
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from .db import new_id, now

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine

GOAL_DONE = "GOAL_COMPLETE:"
GOAL_PROGRESS = "PROGRESS:"
_STATUSES = ("active", "paused", "done", "failed")


class GoalError(ValueError):
    pass


class Goals:
    def __init__(self, engine: "Engine"):
        self.engine = engine
        self.db = engine.db

    # -- CRUD -----------------------------------------------------------------
    def create(self, bot_id: str, title: str, target: str = "", cadence_minutes: int = 360,
               owner: str | None = None) -> dict:
        bot = self.engine.bots.get(bot_id, owner)
        if not bot:
            raise GoalError("No such Bot.")
        title = (title or "").strip()
        if not title:
            raise GoalError("A goal needs a title.")
        th = self.engine.threads.main_thread(bot_id)
        gid = new_id()
        row = {"id": gid, "bot_id": bot_id, "thread_id": th["id"], "owner": owner or "",
               "title": title[:200], "target": (target or "").strip()[:2000],
               "status": "active", "cadence_minutes": max(15, min(60 * 24 * 7, int(cadence_minutes or 360))),
               "next_run_at": now(), "progress": "", "last_report_at": 0.0,
               "created_at": now(), "updated_at": now()}
        self.db.insert("goals", row)
        self.engine.events.publish("goals", change="created", goal_id=gid)
        self._prod(row, first=True)   # kick the first work session off right away
        return self.get(gid, owner)  # type: ignore

    def get(self, gid: str, owner: str | None = None) -> dict | None:
        g = self.db.one("SELECT g.*, b.name AS bot_name, b.emoji AS bot_emoji FROM goals g "
                        "LEFT JOIN bots b ON b.id=g.bot_id WHERE g.id=?", (gid,))
        if not g:
            return None
        if owner and g.get("owner") and g["owner"] != owner:
            return None
        return g

    def list(self, owner: str | None = None) -> list[dict]:
        where, params = [], []
        if owner:
            # Legacy rows have no owner (shared account); those stay visible to everyone.
            where.append("(g.owner=? OR g.owner='')")
            params.append(owner)
        sql = ("SELECT g.*, b.name AS bot_name, b.emoji AS bot_emoji FROM goals g "
               "LEFT JOIN bots b ON b.id=g.bot_id"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY (g.status='active') DESC, g.created_at DESC")
        return self.db.query(sql, params)

    def update(self, gid: str, owner: str | None = None, **fields: Any) -> dict:
        g = self.get(gid, owner)
        if not g:
            raise GoalError("No such goal.")
        patch: dict[str, Any] = {"updated_at": now()}
        if fields.get("title") is not None:
            t = str(fields["title"]).strip()
            if not t:
                raise GoalError("A goal needs a title.")
            patch["title"] = t[:200]
        if fields.get("target") is not None:
            patch["target"] = str(fields["target"]).strip()[:2000]
        if fields.get("cadence_minutes") is not None:
            patch["cadence_minutes"] = max(15, min(60 * 24 * 7, int(fields["cadence_minutes"])))
        if fields.get("status") is not None:
            st = str(fields["status"])
            if st not in _STATUSES:
                raise GoalError("Status must be active, paused, done or failed.")
            patch["status"] = st
            if st == "active":
                patch["next_run_at"] = now()   # resuming prods immediately
        if fields.get("progress") is not None:
            patch["progress"] = str(fields["progress"])[:2000]
        self.db.update("goals", gid, patch)
        self.engine.events.publish("goals", change="updated", goal_id=gid)
        return self.get(gid, owner)  # type: ignore

    def delete(self, gid: str, owner: str | None = None) -> bool:
        g = self.get(gid, owner)
        if not g:
            return False
        self.db.execute("DELETE FROM goals WHERE id=?", (gid,))
        self.engine.events.publish("goals", change="deleted", goal_id=gid)
        return True

    # -- the work loop ----------------------------------------------------------
    def _prod(self, g: dict, first: bool = False) -> None:
        eng = self.engine
        bot = eng.bots.get(g["bot_id"])
        if not bot or bot.get("paused") or bot.get("archived"):
            return
        if eng.turns.is_busy(g["bot_id"]):
            # Bot is mid-something: retry in five minutes instead of queue-piling.
            self.db.update("goals", g["id"], {"next_run_at": now() + 300})
            return
        verb = "Start working on" if first else "Check in on"
        task = (f"[GOAL] {verb} the goal \"{g['title']}\"."
                + (f" The goal is achieved when: {g['target']}." if g["target"] else "")
                + " Look at the real current state first, then do the next useful step (within your approval rules). "
                f"End your reply with {GOAL_PROGRESS} followed by a one-line status of where the goal stands. "
                f"If the goal is now fully achieved, end with {GOAL_DONE} followed by a one-line summary instead.")
        try:
            eng.turns.start(g["bot_id"], g["thread_id"], trigger="goal", task=task)
        except Exception:  # noqa: BLE001
            pass
        self.db.update("goals", g["id"], {"next_run_at": now() + g["cadence_minutes"] * 60, "updated_at": now()})

    def tick(self) -> None:
        """Scheduler job: prod every active goal that is due."""
        try:
            due = self.db.query("SELECT * FROM goals WHERE status='active' AND next_run_at<=?", (now(),))
        except Exception:  # noqa: BLE001
            return
        for g in due:
            try:
                self._prod(g)
            except Exception:  # noqa: BLE001
                continue

    def on_turn_end(self, run: Any) -> None:
        """Called from engine.on_turn_end for goal-triggered turns: harvest progress / completion."""
        res = run.result
        if not res or res.status not in ("done", "limit"):
            return
        g = self.db.one("SELECT * FROM goals WHERE thread_id=? AND status='active' ORDER BY created_at DESC", (run.thread_id,))
        if not g:
            return
        text = res.final_text or ""
        patch: dict[str, Any] = {"last_report_at": now(), "updated_at": now()}
        idx = text.rfind(GOAL_DONE)
        if idx >= 0:
            patch["status"] = "done"
            patch["progress"] = text[idx + len(GOAL_DONE):].strip()[:2000] or "Goal marked complete."
        else:
            idx = text.rfind(GOAL_PROGRESS)
            if idx >= 0:
                patch["progress"] = text[idx + len(GOAL_PROGRESS):].strip()[:2000]
            else:
                # No marker: keep the last line as a lightweight progress note.
                last = [ln for ln in text.strip().splitlines() if ln.strip()]
                if last:
                    patch["progress"] = last[-1][:2000]
        self.db.update("goals", g["id"], patch)
        self.engine.events.publish("goals", change="updated", goal_id=g["id"])

    # -- prompt surface ----------------------------------------------------------
    def prompt_block(self, thread_id: str) -> str:
        """Injected into the system prompt when the current thread carries an active goal."""
        rows = self.db.query("SELECT * FROM goals WHERE thread_id=? AND status='active' ORDER BY created_at DESC", (thread_id,))
        if not rows:
            return ""
        lines = ["Active goal on this thread (work toward it when asked, keep updates one line):"]
        for g in rows[:3]:
            lines.append(f"- {g['title']}" + (f" (done when: {g['target']})" if g["target"] else ""))
        return "\n".join(lines)
