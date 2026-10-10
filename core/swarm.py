"""Swarm mode: fan one instruction out to N bots in parallel, then assemble.

Pure engine-level helper (no Qt, no provider specifics). For each bot it posts
the task into that bot's main thread, starts a turn via TurnManager, waits with
a per-bot deadline, then posts ONE assistant summary into the caller's thread.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .agent import AgentRun
    from .engine import Engine

_PER_BOT_TRUNC = 2000
_STOP_GRACE = 5.0
_FAIL_PREFIXES = ("[error]", "[timeout", "[skipped]", "[stopped]", "[empty]")


def _bot_name(engine: "Engine", bot_id: str) -> str:
    try:
        bot = engine.bots.get(bot_id)
    except Exception:
        bot = None
    if isinstance(bot, dict) and bot.get("name"):
        return str(bot["name"])
    return bot_id


def _build_summary(task: str, bot_ids: list[str], engine: "Engine", results: dict[str, str]) -> str:
    ok_n = sum(1 for b in bot_ids if b in results and not results[b].startswith(_FAIL_PREFIXES))
    lines = [f"Swarm result: {ok_n}/{len(bot_ids)} bots replied.", f"Task: {task.strip()}", ""]
    for bid in bot_ids:
        text = results.get(bid, "[error] No result.")
        if len(text) > _PER_BOT_TRUNC:
            text = text[:_PER_BOT_TRUNC] + "\n[... truncated ...]"
        lines.append(f"--- {_bot_name(engine, bid)} ({bid}) ---")
        lines.append(text)
    return "\n".join(lines).strip()


def _wait_run(run: "AgentRun", timeout_each: int) -> str | None:
    """Wait for a run up to timeout_each seconds. Returns None on success, else an error string."""
    deadline = time.time() + max(1, timeout_each)
    remaining = deadline - time.time()
    done = run.done.wait(timeout=max(0.0, remaining)) if remaining > 0 else run.done.is_set()
    res = run.result
    if done and res is not None:
        if res.status == "error":
            return f"[error] {res.error or 'turn failed'}"
        if res.status == "stopped":
            return "[stopped] Turn was stopped before finishing."
        text = (res.final_text or "").strip()
        return text if text else "[empty] Bot replied with no text."
    try:
        run.stop.set()
    except Exception:
        pass
    run.done.wait(timeout=_STOP_GRACE)
    return f"[timeout after {timeout_each}s] No reply in time."


def run_swarm(engine: "Engine", bot_ids: list[str], task: str,
              reply_thread_id: str = "", timeout_each: int = 600) -> dict:
    """Fan task out to bot_ids in parallel; post one summary into reply_thread_id.

    Returns {"ok": bool, "results": {bot_id: text}, "summary": str}
    (plus "error" on clean failures). One bot's failure never kills the swarm.
    """
    results: dict[str, str] = {}
    if not task or not task.strip():
        return {"ok": False, "results": results, "summary": "", "error": "Empty task."}
    if not bot_ids:
        return {"ok": False, "results": results, "summary": "", "error": "No bots given."}
    pending: list[tuple[str, Any]] = []
    for bid in bot_ids:
        try:
            bot = engine.bots.get(bid)
        except Exception as e:  # noqa: BLE001
            results[bid] = f"[error] Could not look up bot: {e}"
            continue
        if not bot:
            results[bid] = "[error] Unknown bot."
            continue
        try:
            th = engine.threads.main_thread(bid)
            engine.threads.add(th["id"], "user", "user", task)
            run = engine.turns.start(bid, th["id"], trigger="user")
        except Exception as e:  # noqa: BLE001
            results[bid] = f"[error] Could not start turn: {e}"
            continue
        if run is None:
            results[bid] = "[skipped] Bot is paused, archived, or over its usage limit."
            continue
        pending.append((bid, run))
    for bid, run in pending:
        try:
            results[bid] = _wait_run(run, timeout_each) or "[error] No result."
        except Exception as e:  # noqa: BLE001
            results[bid] = f"[error] Wait failed: {e}"
    summary = _build_summary(task, bot_ids, engine, results)
    reply = engine.threads.get(reply_thread_id) if reply_thread_id else None
    if not reply:
        return {"ok": False, "results": results, "summary": summary, "error": "No such reply thread."}
    try:
        engine.threads.add(reply_thread_id, "swarm", "assistant", summary)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "results": results, "summary": summary, "error": f"Could not post summary: {e}"}
    ok = bool(results) and any(not v.startswith(_FAIL_PREFIXES) for v in results.values())
    return {"ok": ok, "results": results, "summary": summary}
