"""Render workspace documents for in-chat/in-app previews.

Guards: only files inside the workspace, only known kinds, and rendering failures
raise a clean RenderError the caller can show.
"""
from __future__ import annotations

import io
from pathlib import Path

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None


class RenderError(RuntimeError):
    pass


def _pd(path: Path):
    if pdfium is None:
        raise RenderError("pypdfium2 is not installed on the server.")
    return pdfium.PdfDocument(str(path))


def pdf_page_png(path: Path, page: int = 1, max_px: int = 1400) -> bytes | None:
    """Render one PDF page to PNG bytes (1-based page). None when out of range."""
    doc = _pd(path)
    try:
        n = len(doc)
        if page < 1 or page > n:
            return None
        img = doc[page - 1].render(scale=2.0)
        pil = img.to_pil()
        w, h = pil.size
        long_edge = max(w, h)
        if long_edge > max_px:
            r = max_px / long_edge
            pil = pil.resize((int(w * r), int(h * r)))
        bufs = io.BytesIO()
        pil.save(bufs, format="PNG")
        return bufs.getvalue()
    finally:
        doc.close()


def pdf_page_count(path: Path) -> int:
    doc = _pd(path)
    try:
        return len(doc)
    finally:
        doc.close()


def pdf_text(path: Path, limit: int = 200_000) -> str:
    from pypdf import PdfReader
    try:
        r = PdfReader(str(path))
        chunks = []
        for pg in r.pages:
            try:
                chunks.append(pg.extract_text() or "")
            except Exception:  # noqa: BLE001
                pass
        return "\n\n".join(chunks)[:limit]
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Could not read the PDF: {exc}")


def docx_text(path: Path, limit: int = 200_000) -> str:
    try:
        from docx import Document
        d = Document(str(path))
        out = []
        for para in d.paragraphs:
            if para.text.strip():
                st = para.style.name.lower() if para.style else ""
                if "heading" in st:
                    out.append("\n" + para.text)
                else:
                    out.append(para.text)
        for t in d.tables:
            out.append("")
            for row in t.rows:
                out.append(" | ".join(c.text for c in row.cells))
        return "\n".join(out)[:limit]
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Could not read the DOCX: {exc}")


def inspect(path: Path) -> dict:
    """Classify a workspace file for the preview endpoints."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            return {"kind": "pdf", "pages": pdf_page_count(path)}
        except RenderError:
            return {"kind": "pdf", "pages": None}
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return {"kind": "image"}
    if suffix == ".docx":
        try:
            return {"kind": "docx", "text": docx_text(path)}
        except RenderError:
            return {"kind": "binary"}
    text_exts = {".txt", ".md", ".csv", ".json", ".log", ".py", ".xml", ".html", ".htm",
                 ".yaml", ".yml", ".tsv", ".ini", ".cfg", ".rs", ".js", ".ts", ".css"}
    if suffix in text_exts and path.stat().st_size <= 4_000_000:
        raw = path.read_bytes()[:800_000]
        try:
            return {"kind": "text", "text": raw.decode("utf-8")}
        except UnicodeDecodeError:
            pass
    return {"kind": "binary"}
