"""Google connectors: Docs/Sheets/Slides tools + Gmail attachment read/attach.

Run: python -m unittest tests.test_gdocs -v
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.plugins import Conn, PluginDef, build_gdocs, build_gmail, builtin_defs  # noqa: E402

PDF_BYTES = b"%PDF-1.4 fake curriculum vitae"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class FakeConn:
    """Enough of Conn for tool builders: routes HTTP to canned responses, records requests."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests: list[dict] = []

    def google_headers(self):
        return {"Authorization": "Bearer fake"}

    def http(self, ctx, method, url, *, headers=None, params=None, json_body=None, data=None, timeout=30):
        self.requests.append({"method": method, "url": url, "params": params, "json": json_body})
        for key, resp in self.routes.items():
            if key in url:
                if callable(resp):
                    return resp(method, url, params, json_body)
                return resp
        raise AssertionError(f"unrouted url: {url}")


def fake_ctx(workspace: Path):
    return SimpleNamespace(engine=SimpleNamespace(computer=SimpleNamespace(workspace=workspace)))


def handler_of(tools, name):
    return next(t for t in tools if t.name == name).handler


class DefTests(unittest.TestCase):
    def test_gdocs_def_and_scopes(self):
        d = {x.id: x for x in builtin_defs()}
        self.assertIn("gdocs", d)
        scopes = d["gdocs"].oauth["scopes"]
        for sc in ("documents", "spreadsheets", "presentations", "drive.file"):
            self.assertTrue(any(sc in s for s in scopes), sc)

    def test_gmail_scope_still_covers_attachments(self):
        d = {x.id: x for x in builtin_defs()}
        self.assertIn("gmail.modify", " ".join(d["gmail"].oauth["scopes"]))

    def test_tool_names_and_risk_gates(self):
        gtools = {t.name: t for t in build_gmail(None)}
        self.assertIn("gmail_get_attachment", gtools)
        self.assertTrue(gtools["gmail_send"].input_schema["properties"].get("attachments"))
        dtools = {t.name: t for t in build_gdocs(None)}
        self.assertEqual(len(dtools), 8)
        for n in ("gdocs_create", "gdocs_append", "gsheets_create", "gsheets_append", "gslides_create"):
            self.assertIsNotNone(dtools[n].risk, n)
        for n in ("gdocs_read", "gsheets_read", "gdocs_search"):
            self.assertTrue(dtools[n].read_only, n)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="gbatt-"))
        self.ctx = fake_ctx(self.ws)
        self.conn = FakeConn(routes={
            "/messages/msg1/attachments/att1": {"data": b64url(PDF_BYTES)},
            "/drafts": {"id": "draft1"},
            "/messages/msg1": {"id": "msg1", "threadId": "t1", "payload": {"headers": [], "parts": [
                {"filename": "cv-master.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "att1", "size": len(PDF_BYTES)}},
                {"mimeType": "text/plain", "body": {"data": b64url(b"plain body")}},
            ]}},
        })
        self.tools = build_gmail(self.conn)

    def test_read_lists_attachments(self):
        out = handler_of(self.tools, "gmail_read")(self.ctx, {"message_id": "msg1"})
        data = json.loads(out.text) if isinstance(out.text, str) and out.text.startswith("{") else out
        # T wraps dicts as json text
        payload = json.loads(out.text)
        self.assertEqual(payload["attachments"][0]["filename"], "cv-master.pdf")
        self.assertEqual(payload["attachments"][0]["attachment_id"], "att1")

    def test_get_attachment_saves_file(self):
        out = handler_of(self.tools, "gmail_get_attachment")(self.ctx, {"message_id": "msg1", "filename": "cv-master.pdf"})
        self.assertIn("Saved", out.text)
        fp = self.ws / "shared" / "attachments" / "msg1" / "cv-master.pdf"
        self.assertTrue(fp.is_file())
        self.assertEqual(fp.read_bytes(), PDF_BYTES)

    def test_get_attachment_missing_name_lists_available(self):
        out = handler_of(self.tools, "gmail_get_attachment")(self.ctx, {"message_id": "msg1", "filename": "nope.docx"})
        data = json.loads(out.text)
        self.assertIn("error", data)
        self.assertIn("cv-master.pdf", data["available"])

    def test_draft_builds_mime_with_attachment(self):
        cv = self.ws / "shared" / "jobhunt" / "cv-master.pdf"
        cv.parent.mkdir(parents=True)
        cv.write_bytes(PDF_BYTES)
        out = handler_of(self.tools, "gmail_create_draft")(self.ctx, {
            "to": "hr@example.com", "subject": "Application", "body": "Please find my CV attached.",
            "attachments": ["shared/jobhunt/cv-master.pdf"]})
        self.assertIn("Draft created", out.text)
        raw = self.conn.requests[-1]["json"]["message"]["raw"]
        raw += "=" * (-len(raw) % 4)
        mime = base64.urlsafe_b64decode(raw)
        self.assertIn(b"cv-master.pdf", mime)
        self.assertIn(base64.b64encode(PDF_BYTES).rstrip(b"="), mime)


class GdocsTests(unittest.TestCase):
    def setUp(self):
        self.ctx = fake_ctx(Path(tempfile.mkdtemp(prefix="gbdoc-")))
        self.conn = FakeConn(routes={
            "spreadsheets/sh1": {},
            "docs.googleapis.com/v1/documents/doc123": {"documentId": "doc123", "title": "Cover letter",
                                                         "body": {"content": [
                                                             {"paragraph": {"paragraphElements": [{"textRun": {"content": "Dear HR,\n"}}]}},
                                                             {"paragraph": {"paragraphElements": [{"textRun": {"content": "Yours truly."}}]}},
                                                         ]}},
            "slides.googleapis.com/v1/presentations": lambda m, u, p, j: {"presentationId": "pres456"},
        })
        self.tools = build_gdocs(self.conn)

    def test_doc_read_joins_paragraphs(self):
        out = handler_of(self.tools, "gdocs_read")(self.ctx, {"document_id": "doc123"})
        data = json.loads(out.text)
        self.assertEqual(data["title"], "Cover letter")
        self.assertIn("Dear HR,", data["text"])
        self.assertIn("Yours truly.", data["text"])

    def test_doc_append_posts_insert_text(self):
        handler_of(self.tools, "gdocs_append")(self.ctx, {"document_id": "doc123", "text": "Extra line."})
        req = self.conn.requests[-1]
        self.assertIn(":batchUpdate", req["url"])
        self.assertEqual(req["json"]["requests"][0]["insertText"]["text"], "Extra line.")

    def test_sheet_append_sends_row(self):
        handler_of(self.tools, "gsheets_append")(self.ctx, {"spreadsheet_id": "sh1", "cells": ["a", "b"]})
        req = self.conn.requests[-1]
        self.assertIn(":append", req["url"])
        self.assertEqual(req["json"]["values"], [["a", "b"]])

    def test_slide_create_builds_batch(self):
        out = handler_of(self.tools, "gslides_create")(self.ctx, {"title": "Weekly report",
                                                                  "slides": [{"title": "Wins", "bullets": ["Shipped X"]}]})
        self.assertIn("pres456", out.text)


if __name__ == "__main__":
    unittest.main()
