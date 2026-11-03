"""PDF and document parser for extracting action items.

Parses PDFs, images, and documents attached to emails or uploaded by users.
Detects form fields, dates, monetary amounts, and action items
(sign here, fill this out, approve by date), then creates approvals
for user review — matching Muse's "Found the field trip form in your
email due today, so I filled it out" capability.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from core.db import new_id, now


class DocumentParser:
    """Extract actionable items from PDFs and documents."""

    # ---------------------------------------------------------------- text extraction
    def extract_text(self, file_path: str) -> str:
        """Extract text from a PDF or document file."""
        try:
            import PyPDF2  # type: ignore
        except ImportError:
            return ""

        text_parts = []
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text_parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            return ""
        return "\n".join(text_parts)

    # ---------------------------------------------------------------- action detection
    def detect_actions(self, text: str, file_name: str = "") -> list[dict]:
        """Find action items in document text."""
        actions = []

        # Date patterns
        date_patterns = [
            r"(?:due|by|before|until|no later than)\s+(.{1,30})",
            r"(?:deadline|expires?)\s+(.{1,30})",
        ]
        for pattern in date_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                actions.append({
                    "type": "deadline",
                    "urgency": "high",
                    "summary": f"Deadline in {file_name}: {match.group(1).strip()[:80]}",
                    "details": match.group(0),
                })
                break  # one deadline per doc

    # Form fields (sign here, fill out)
        if re.search(r"(?:sign|initial|fill|complete)\s+here", text, re.IGNORECASE):
            actions.append({
                "type": "form",
                "urgency": "high",
                "summary": f"Form needs signature: {file_name or 'document'}",
                "details": "Signature or form completion required",
            })

        # Monetary amounts
        amount_match = re.search(r"\$[\d,]+\.?\d*", text)
        if amount_match and any(k in text.lower() for k in ("total", "amount", "pay", "cost", "price")):
            actions.append({
                "type": "purchase",
                "urgency": "medium",
                "summary": f"Payment required: {amount_match.group(0)}",
                "details": f"Amount: {amount_match.group(0)}",
            })

        return actions

    # ---------------------------------------------------------------- create approvals
    def create_approvals_from_doc(self, file_path: str, file_name: str,
                                  owner_email: str, engine) -> list[str]:
        """Parse a document and create approval requests for detected actions."""
        text = self.extract_text(file_path)
        if not text:
            return []

        actions = self.detect_actions(text, file_name)
        if not actions:
            return []

        # Find user's main bot
        bots = engine.bots.list(owner=owner_email)
        if not bots:
            return []
        bot = bots[0]

        created = []
        from core.approvals import ApprovalManager
        ap = ApprovalManager(engine.db, engine.events, engine.settings, engine.admin)
        ap.engine = engine

        for action in actions:
            thread = engine.threads.create(
                bot_id=bot["id"],
                title=f"Document: {file_name}",
                kind="dm",
                owner=owner_email,
            )

            ctx = type("Ctx", (), {
                "bot": bot, "thread_id": thread["id"], "turn_id": "",
                "run": type("Run", (), {
                    "tainted": False,
                    "stop": type("Stop", (), {"is_set": lambda self: False})(),
                })(),
            })()

            risk = type("Risk", (), {
                "category": action["type"],
                "details": {
                    "pattern": "*", "merchant": "", "item": file_name,
                    "image_url": "", "action": "document_action",
                },
                "summary": action["summary"],
                "never_auto": True,
            })()

            decision = ap.request(ctx, risk, "document_action", {})
            if decision.approved is False:
                created.append(action["summary"])
        return created
