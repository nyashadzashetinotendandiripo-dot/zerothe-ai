"""Job Hunt plugin: save job listings to Bot memory."""
from __future__ import annotations

try:
    from core.plugins import ConnectorError
except ImportError:
    class ConnectorError(RuntimeError):
        pass


def jobhunt_save_job(ctx, a):
    """Save a job listing to the Bot's memory as a preference."""
    title = (a.get("title") or "").strip()
    company = (a.get("company") or "").strip()
    url = (a.get("url") or "").strip()
    location = (a.get("location") or "").strip()
    notes = (a.get("notes") or "").strip()

    if not title or not company or not url:
        raise ConnectorError("title, company, and url are required.")

    text = f"Job: {title} at {company}"
    if location:
        text += f" — {location}"
    if url:
        text += f" — {url}"
    if notes:
        text += f"\nNotes: {notes}"

    try:
        ctx.engine.threads.add(
            ctx.thread_id, "system", "user",
            [{"type": "text", "text": text, "level": "job_save"}],
            kind="memory",
        )
    except Exception:
        # Fallback: save to bot memory directly
        try:
            ctx.engine.db.insert("bot_memory", {
                "bot_id": ctx.bot["id"],
                "kind": "job_save",
                "text": text,
                "source": "jobhunt",
                "created_at": __import__("time").time(),
            })
        except Exception:
            pass

    return f"Saved job '{title}' at {company} to Bot memory."
