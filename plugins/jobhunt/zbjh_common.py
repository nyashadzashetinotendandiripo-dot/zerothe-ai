"""Job Hunt shared helpers: workspace paths, profile, job store, HTTP, browser, LLM."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

try:
    from core.plugins import ConnectorError
    from core.tooling import ToolResult
except ImportError:  # pragma: no cover - allows running the plugin standalone
    class ConnectorError(RuntimeError):
        pass

    from dataclasses import dataclass, field

    @dataclass
    class ToolResult:
        text: str = ""
        data: str = ""
        images: list = field(default_factory=list)
        files: list = field(default_factory=list)
        is_error: bool = False
        url: str = ""
        path: str = ""
        untrusted: str = ""


BASE = "shared/jobhunt"
PROFILE_PATH = BASE + "/profile.json"
JOBS_PATH = BASE + "/jobs.json"
CV_DIR = BASE + "/cvs"
LETTER_DIR = BASE + "/cover-letters"
APP_DIR = BASE + "/applications"
CERT_DIR = BASE + "/certificates"

_API = None


def set_api(api) -> None:
    global _API
    _API = api


def api_http(ctx, method: str, url: str, **kw):
    if _API is None:
        raise ConnectorError("Job Hunt is not initialised.")
    return _API.http(ctx, method, url, **kw)


# ---------------------------------------------------------------- small utilities
def ws(ctx) -> Path:
    return Path(ctx.engine.computer.workspace)


def resolve(ctx, rel: str) -> Path:
    p = Path(str(rel))
    if not p.is_absolute():
        p = ws(ctx) / p
    return p


def rel(ctx, p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(ws(ctx).resolve())).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


def ensure_parent(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def slug(s: str, fallback: str = "file") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(s or "")).strip("-").lower()
    return (s[:70] or fallback)


def clamp(n, low: int, high: int) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = low
    return max(low, min(high, n))


def now_ts() -> int:
    return int(time.time())


def today_str(fmt: str = "%Y-%m-%d") -> str:
    return date.today().strftime(fmt)


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(p: Path, data) -> None:
    ensure_parent(p).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    for i in range(2, 200):
        q = p.with_name(f"{stem}-{i}{suffix}")
        if not q.exists():
            return q
    return p.with_name(f"{stem}-{int(time.time())}{suffix}")


def as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, dict):
        return [{"name": k, **(v2 if isinstance(v2, dict) else {"value": v2})} for k, v in v.items()]
    text = str(v).strip()
    if not text:
        return []
    lines = [x.strip(" -•\t") for x in text.splitlines() if x.strip(" -•\t")]
    if len(lines) <= 1:
        lines = [x.strip() for x in re.split(r"[;\u2022]+", text) if x.strip()]
    return lines


def one_line(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def with_article(role: str, lower: bool = False) -> str:
    """'Accountant' -> 'an Accountant'; keeps titles that already start with an article."""
    role = collapse(role)
    if not role:
        return "a professional"
    if re.match(r"(?i)^(a|an|the)\b", role):
        return role if not lower else role[0].lower() + role[1:]
    article = "an" if role[:1].lower() in "aeiou" else "a"
    if re.match(r"(?i)^(hr|it|m&e|qa|qc|ceo|cfo|coo|md|pa)\b", role):
        article = "an"
    out = f"{article} {role}"
    return out if not lower else out


def esc(s) -> str:
    """Escape text for reportlab Paragraph markup."""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\u2019", "'").replace("\u2018", "'"))


def strip_html(html_text: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text or "", "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        text = soup.get_text("\n")
    except Exception:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text or "")
        text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def read_doc_text(ctx, rel_path: str) -> str:
    p = resolve(ctx, rel_path)
    if not p.is_file():
        raise ConnectorError(f"No such file: {rel_path}")
    ext = p.suffix.lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ConnectorError("pypdf is not installed on the server.")
        reader = PdfReader(str(p))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise ConnectorError("python-docx is not installed on the server.")
        doc = Document(str(p))
        parts = [para.text for para in doc.paragraphs if para.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    return p.read_text(encoding="utf-8", errors="replace")


def file_info(ctx, p: Path) -> dict:
    st = p.stat()
    return {"path": rel(ctx, p), "name": p.name, "size": st.st_size,
            "mime": __import__("mimetypes").guess_type(p.name)[0] or "application/octet-stream"}


# ---------------------------------------------------------------- HTTP + browser
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def fetch_text(ctx, url: str, *, headers: dict | None = None, timeout: float = 30) -> str:
    h = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
    if headers:
        h.update(headers)
    data = api_http(ctx, "GET", url, headers=h, timeout=timeout)
    if isinstance(data, (dict, list)):
        return json.dumps(data)
    return str(data or "")


def fetch_json(ctx, url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = 30):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    data = api_http(ctx, "GET", url, params=params, headers=h, timeout=timeout)
    if isinstance(data, (dict, list)):
        return data
    try:
        return json.loads(str(data or "null"))
    except ValueError:
        raise ConnectorError(f"Expected JSON from {url}, got text.")


def browser_page(ctx):
    br = ctx.engine.computer.browser
    scr = br.screen(ctx.bot["id"])
    page = br.call(scr.ensure_page)
    return br, scr, page


def browser_html(ctx, url: str, wait_ms: int = 1800) -> str:
    br = ctx.engine.computer.browser
    scr = br.screen(ctx.bot["id"])
    br.call(scr.goto, url)
    page = br.call(scr.ensure_page)
    try:
        br.call(page.wait_for_timeout, wait_ms)
    except Exception:
        pass
    return br.call(page.content)


def get_html(ctx, url: str, *, use_browser: str = "auto", wait_ms: int = 1800) -> tuple[str, str]:
    """Fetch a page. Returns (html, method). Falls back to the Bot browser."""
    if use_browser != "always":
        try:
            text = fetch_text(ctx, url)
            low = text[:400].lower()
            if len(text) > 500 and "enable javascript" not in low and "cf-browser-verification" not in low:
                return text, "http"
        except Exception:
            if use_browser == "never":
                raise
    if use_browser == "never":
        raise ConnectorError("The site did not return usable HTML over HTTP.")
    return browser_html(ctx, url, wait_ms), "browser"


# ---------------------------------------------------------------- profile
DEFAULT_PROFILE = {
    "full_name": "", "headline": "", "email": "", "phone": "", "location": "", "country": "ZW",
    "address": "", "linkedin": "", "portfolio": "", "github": "",
    "nationality": "", "date_of_birth": "", "gender": "", "id_number": "", "drivers_licence": "",
    "languages": ["English (fluent)"], "summary": "", "skills": [],
    "experience": [], "education": [], "certifications": [], "projects": [], "referees": [],
    "documents": {"certificates": [], "transcripts": [], "other": []},
    "preferences": {"titles": [], "locations": [], "regions": ["zw", "za"], "remote": True,
                    "min_salary": "", "job_types": []},
    "default_region": "zw", "writing_style": "plain, warm, professional",
    "work_authorization": "", "notice_period": "", "available_from": "",
}


def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_profile(ctx) -> dict:
    p = load_json(resolve(ctx, PROFILE_PATH), {})
    return deep_merge(DEFAULT_PROFILE, p if isinstance(p, dict) else {})


def save_profile(ctx, data: dict) -> dict:
    prof = deep_merge(load_profile(ctx), data or {})
    save_json(resolve(ctx, PROFILE_PATH), prof)
    return prof


def profile_skills(prof: dict) -> list[str]:
    sk = prof.get("skills")
    if isinstance(sk, dict):
        out = []
        for v in sk.values():
            out.extend(as_list(v))
        return out
    return as_list(sk)


def profile_summary_text(prof: dict) -> str:
    lines = []
    for key in ("full_name", "headline", "email", "phone", "location", "nationality"):
        if prof.get(key):
            lines.append(f"{key.replace('_', ' ').title()}: {prof[key]}")
    if prof.get("summary"):
        lines.append("Summary: " + collapse(prof["summary"]))
    if prof.get("skills"):
        lines.append("Skills: " + ", ".join(profile_skills(prof)))
    for e in as_list(prof.get("experience")):
        if isinstance(e, dict):
            lines.append(f"Experience: {e.get('title', '')} at {e.get('company', '')} ({e.get('start', '')}-{e.get('end', '')})")
            for b in as_list(e.get("bullets") or e.get("achievements")):
                lines.append("  - " + collapse(b))
        else:
            lines.append("Experience: " + collapse(e))
    for ed in as_list(prof.get("education")):
        if isinstance(ed, dict):
            lines.append(f"Education: {ed.get('qualification', '')} - {ed.get('institution', '')} ({ed.get('year', ed.get('end', ''))})")
        else:
            lines.append("Education: " + collapse(ed))
    return "\n".join(lines)[:12000]


# ---------------------------------------------------------------- job store
def load_jobs(ctx) -> dict:
    data = load_json(resolve(ctx, JOBS_PATH), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("jobs", {})
    return data


def save_jobs(ctx, data: dict) -> None:
    data["updated"] = now_ts()
    save_json(resolve(ctx, JOBS_PATH), data)


def job_id(url: str, title: str = "", company: str = "") -> str:
    raw = (url or "") + "|" + (title or "") + "|" + (company or "")
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:10]


def upsert_job(ctx, job: dict) -> dict:
    data = load_jobs(ctx)
    jobs = data["jobs"]
    jid = job.get("id") or job_id(job.get("url", ""), job.get("title", ""), job.get("company", ""))
    old = jobs.get(jid, {})
    merged = dict(old)
    merged.update({k: v for k, v in job.items() if v not in (None, "", [])})
    merged["id"] = jid
    merged.setdefault("status", "saved")
    merged.setdefault("first_seen", now_ts())
    merged["last_seen"] = now_ts()
    jobs[jid] = merged
    save_jobs(ctx, data)
    return merged


def get_job(ctx, jid: str) -> dict | None:
    data = load_jobs(ctx)
    job = data["jobs"].get(jid)
    if job:
        return job
    for j in data["jobs"].values():
        if j.get("url") == jid:
            return j
    return None


# ---------------------------------------------------------------- LLM helper
def llm(ctx, system: str, user: str, max_tokens: int = 2000) -> str:
    """One-shot model call using the Bot's own provider. Returns '' on any failure."""
    try:
        from core.providers import make_provider
        bot = ctx.bot or {}
        provider = make_provider(ctx.engine.settings, bot.get("profile") or None, bot.get("model") or None)
        res = provider.stream(system, [{"role": "user", "content": [{"type": "text", "text": user}]}],
                              [], lambda _t: None, lambda: False, max_tokens=max_tokens)
        try:
            ctx.engine.usage.record(bot.get("id", ""), getattr(ctx, "turn_id", "") or "",
                                    provider.profile.get("id", ""), provider.model,
                                    res.input_tokens, res.output_tokens)
        except Exception:
            pass
        return (res.text or "").strip()
    except Exception:
        return ""


def json_from(text: str) -> dict:
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def quote(s: str) -> str:
    return quote_plus(str(s or ""))


def domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def abs_url(base: str, href: str) -> str:
    try:
        return urljoin(base, href or "")
    except Exception:
        return href or ""
