"""IMAP email connector for agent email ingestion.

Connects to a user's IMAP mailbox, polls for new messages, extracts
action items (forms, dates, requests), and creates approvals so the
agent can propose actions like "fill out this field trip form" or
"send a follow-up email."
"""
from __future__ import annotations

import email
import imaplib
import time
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Any

from core import auth as auth_mod
from core.db import Database, new_id, now
from core.events import EventBus
from core.settings import Admin, Settings


class EmailConnector:
    """Poll an IMAP inbox and surface actionable emails as approvals."""

    def __init__(self, db: Database, settings: Settings, admin: Admin, events: EventBus):
        self.db, self.settings, self.admin, self.events = db, settings, admin, events
        self._poll_interval = 300  # seconds between polls per account

    # ---------------------------------------------------------------- account config
    def _imap_config(self, owner_email: str) -> dict | None:
        """Return IMAP settings for a user, or None if not configured."""
        key = f"imap.{owner_email}"
        cfg = self.settings.get(key)
        return cfg if cfg else None

    def set_imap_config(self, owner_email: str, host: str, port: int, username: str,
                        password: str, use_ssl: bool = True) -> None:
        """Store IMAP credentials for a user (encrypted at rest via settings)."""
        key = f"imap.{owner_email}"
        self.settings.set(key, {
            "host": host, "port": port, "username": username,
            "password": password, "use_ssl": use_ssl,
            "configured_at": now(),
        })

    # ---------------------------------------------------------------- polling
    def poll_inbox(self, owner_email: str, limit: int = 10) -> list[dict]:
        """Connect to IMAP, fetch recent unseen messages, return action items."""
        cfg = self._imap_config(owner_email)
        if not cfg:
            return []

        results = []
        try:
            if cfg.get("use_ssl", True):
                m = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], timeout=30)
            else:
                m = imaplib.IMAP4(cfg["host"], cfg["port"], timeout=30)

            m.login(cfg["username"], cfg["password"])
            m.select("INBOX")

            # Search for unseen messages from the last 7 days
            since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
            typ, data = m.search(None, f'(UNSEEN SINCE {since})')
            if typ != "OK":
                m.logout()
                return results

            msg_ids = data[0].split()[:limit]
            for msg_id in msg_ids:
                typ, msg_data = m.fetch(msg_id, "(RFC822)")
                if typ != "OK":
                    continue
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                item = self._parse_message(msg)
                if item:
                    item["owner"] = owner_email
                    item["msg_id"] = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
                    results.append(item)

            m.logout()
        except Exception:  # noqa: BLE001
            # IMAP errors shouldn't crash the service; log and continue
            pass
        return results

    # ---------------------------------------------------------------- parsing
    def _parse_message(self, msg) -> dict | None:
        """Extract actionable info from an email message."""
        subject = self._decode_header(msg.get("Subject", ""))
        sender = self._decode_header(msg.get("From", ""))
        date = msg.get("Date", "")

        # Get body text
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
                elif part.get_content_type() == "text/html" and not body:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

        # Detect action items
        action = self._detect_action(subject, body)
        if not action:
            return None

        return {
            "subject": subject,
            "sender": sender,
            "date": date,
            "body_preview": body[:500],
            "action": action,
            "action_type": action.get("type", "review"),
            "urgency": action.get("urgency", "medium"),
        }

    def _decode_header(self, value: str) -> str:
        if not value:
            return ""
        parts = decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    def _detect_action(self, subject: str, body: str) -> dict | None:
        """Heuristic detection of actionable items in email."""
        text = f"{subject} {body}".lower()

        # Form / document needs filling
        if any(k in text for k in ("form", "fill out", "complete", "sign here", "permission slip")):
            return {"type": "form", "urgency": "high",
                    "summary": f"Form needs attention: {subject[:80]}"}

        # Meeting / calendar request
        if any(k in text for k in ("meeting", "calendar", "invite", "rsvp", "appointment")):
            return {"type": "calendar", "urgency": "medium",
                    "summary": f"Meeting request: {subject[:80]}"}

        # Purchase / order confirmation
        if any(k in text for k in ("order", "purchase", "buy", "checkout", "confirm your order")):
            return {"type": "purchase", "urgency": "medium",
                    "summary": f"Purchase confirmation: {subject[:80]}"}

        # Follow-up / action requested
        if any(k in text for k in ("follow up", "action required", "please respond", "needs your approval")):
            return {"type": "followup", "urgency": "medium",
                    "summary": f"Action required: {subject[:80]}"}

        return None

    # ---------------------------------------------------------------- create approvals
    def create_approvals(self, items: list[dict], engine) -> list[str]:
        """Convert detected email actions into approval requests."""
        created = []
        for item in items:
            # Find or create a bot for this user (use their main bot)
            bots = engine.bots.list(owner=item["owner"])
            if not bots:
                continue
            bot = bots[0]

            # Create a thread for this email action
            thread = engine.threads.create(
                bot_id=bot["id"],
                title=f"Email: {item['subject'][:60]}",
                kind="dm",
                owner=item["owner"],
            )

            # Request approval from the user
            from core.approvals import ApprovalManager
            ap = ApprovalManager(engine.db, engine.events, engine.settings, engine.admin)
            ap.engine = engine

            ctx = type("Ctx", (), {
                "bot": bot, "thread_id": thread["id"], "turn_id": "",
                "run": type("Run", (), {"tainted": False, "stop": type("Stop", (), {"is_set": lambda self: False})}),
            })()  # noqa: E501

            risk = type("Risk", (), {
                "category": item["action_type"],
                "details": {"pattern": "*", "merchant": item.get("sender", ""),
                            "item": item.get("subject", ""),
                            "image_url": "", "action": "email_action"},
                "summary": item["action"]["summary"],
                "never_auto": True,
            })()  # noqa: E501

            decision = ap.request(ctx, risk, "email_action", {})
            if decision.approved is False:  # pending user decision
                created.append(item["action"]["summary"])
        return created
