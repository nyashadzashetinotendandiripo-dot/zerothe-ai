"""Job Hunt: region-aware CV and cover-letter creation, tailoring and document management."""
from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

import zbjh_common as C
from zbjh_common import ConnectorError, ToolResult

ASSETS = Path(__file__).resolve().parent / "assets"

# ---------------------------------------------------------------- region conventions
DEFAULT_REGION = {
    "label": "International", "paper": "A4", "photo": False, "dob": False, "nationality": False,
    "id_number": False, "marital": False, "referees": "on_request", "pages": 2, "font": "Calibri",
    "date_fmt": "dmy_text", "spelling": "en-GB", "phone": "", "tone": "professional",
    "currency": "", "personal": False, "cover": "formal",
}

REGIONS = {
    "zw": {"label": "Zimbabwe", "paper": "A4", "photo": False, "dob": True, "nationality": True,
           "id_number": False, "referees": "full", "pages": 2, "font": "Calibri", "date_fmt": "dmy_text",
           "spelling": "en-GB", "phone": "+263", "tone": "warm-formal", "currency": "USD", "personal": True,
           "cover": "formal"},
    "za": {"label": "South Africa", "paper": "A4", "photo": False, "dob": True, "nationality": True,
           "id_number": False, "referees": "full", "pages": 2, "font": "Calibri", "date_fmt": "dmy_text",
           "spelling": "en-GB", "phone": "+27", "tone": "warm-formal", "currency": "ZAR", "personal": True,
           "cover": "formal"},
    "africa": {"label": "Africa", "paper": "A4", "photo": False, "dob": True, "nationality": True,
               "id_number": False, "referees": "full", "pages": 2, "font": "Calibri", "date_fmt": "dmy_text",
               "spelling": "en-GB", "phone": "", "tone": "warm-formal", "currency": "", "personal": True,
               "cover": "formal"},
    "uk": {"label": "United Kingdom", "paper": "A4", "photo": False, "dob": False, "nationality": False,
           "id_number": False, "referees": "on_request", "pages": 2, "font": "Calibri", "date_fmt": "dmy_text",
           "spelling": "en-GB", "phone": "+44", "tone": "professional", "currency": "GBP", "personal": False,
           "cover": "formal"},
    "europe": {"label": "Europe", "paper": "A4", "photo": False, "dob": False, "nationality": True,
               "id_number": False, "referees": "on_request", "pages": 2, "font": "Calibri", "date_fmt": "dmy_text",
               "spelling": "en-GB", "phone": "", "tone": "professional", "currency": "EUR", "personal": True,
               "cover": "formal"},
    "de": {"label": "Germany", "paper": "A4", "photo": True, "dob": True, "nationality": True,
           "id_number": False, "referees": "on_request", "pages": 2, "font": "Calibri", "date_fmt": "dmy_dot",
           "spelling": "en-GB", "phone": "+49", "tone": "very-formal", "currency": "EUR", "personal": True,
           "cover": "very-formal"},
    "fr": {"label": "France", "paper": "A4", "photo": True, "dob": True, "nationality": True,
           "id_number": False, "referees": "on_request", "pages": 1, "font": "Calibri", "date_fmt": "dmy_text",
           "spelling": "en-GB", "phone": "+33", "tone": "formal", "currency": "EUR", "personal": True,
           "cover": "formal"},
    "us": {"label": "United States / Canada", "paper": "Letter", "photo": False, "dob": False,
           "nationality": False, "id_number": False, "referees": "on_request", "pages": 1, "font": "Calibri",
           "date_fmt": "mdy_text", "spelling": "en-US", "phone": "+1", "tone": "direct", "currency": "USD",
           "personal": False, "cover": "direct"},
    "au": {"label": "Australia / New Zealand", "paper": "A4", "photo": False, "dob": False, "nationality": False,
           "id_number": False, "referees": "on_request", "pages": 2, "font": "Calibri", "date_fmt": "dmy_text",
           "spelling": "en-GB", "phone": "+61", "tone": "direct", "currency": "AUD", "personal": False,
           "cover": "direct"},
    "gulf": {"label": "Gulf / Middle East", "paper": "A4", "photo": True, "dob": True, "nationality": True,
             "id_number": False, "marital": True, "referees": "full", "pages": 2, "font": "Calibri",
             "date_fmt": "dmy_text", "spelling": "en-GB", "phone": "+971", "tone": "formal", "currency": "AED",
             "personal": True, "cover": "formal"},
}

REGION_ALIASES = {
    "zimbabwe": "zw", "zim": "zw", "south africa": "za", "rsa": "za", "sa": "za", "africa": "africa",
    "kenya": "africa", "nigeria": "africa", "ghana": "africa", "zambia": "africa", "botswana": "africa",
    "united kingdom": "uk", "england": "uk", "britain": "uk", "scotland": "uk", "wales": "uk",
    "europe": "europe", "eu": "europe", "ireland": "europe", "netherlands": "europe", "spain": "europe",
    "italy": "europe", "portugal": "europe", "belgium": "europe", "switzerland": "europe", "austria": "europe",
    "germany": "de", "deutschland": "de", "france": "fr",
    "united states": "us", "usa": "us", "us": "us", "canada": "us", "america": "us",
    "australia": "au", "new zealand": "au", "nz": "au",
    "uae": "gulf", "dubai": "gulf", "abu dhabi": "gulf", "saudi arabia": "gulf", "qatar": "gulf", "gulf": "gulf",
}


def region_profile(name: str | None) -> dict:
    key = (name or "zw").strip().lower().replace("_", " ").replace("-", " ")
    key = REGION_ALIASES.get(key, key)
    out = dict(DEFAULT_REGION)
    out.update(REGIONS.get(key, {}))
    out["key"] = key if key in REGIONS else "africa"
    return out


def fmt_date(value: str, region: dict) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %Y", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw[:10] if "%d" in fmt or "%m" in fmt else raw, fmt)
            break
        except ValueError:
            continue
    if not parsed:
        return raw
    style = region.get("date_fmt", "dmy_text")
    if style == "dmy_dot":
        return parsed.strftime("%d.%m.%Y")
    if style == "dmy_slash":
        return parsed.strftime("%d/%m/%Y")
    if style == "mdy_text":
        return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def _date_range(start: str, end: str, region: dict) -> str:
    a = fmt_date(start, region)
    b = fmt_date(end, region) if end else ""
    if a and b:
        return f"{a} - {b}"
    return a or b or ""


# ---------------------------------------------------------------- fonts
_FONT_CACHE: dict[str, str] = {}


def _font_paths(family: str, serif: bool = False) -> list[tuple[Path, Path, Path, Path]]:
    sys_fonts = Path("/usr/share/fonts/truetype")
    dejavu = sys_fonts / "dejavu"
    carlito = [
        (ASSETS / "Carlito-Regular.ttf", ASSETS / "Carlito-Bold.ttf", ASSETS / "Carlito-Italic.ttf", ASSETS / "Carlito-BoldItalic.ttf"),
        (sys_fonts / "crosextra" / "Carlito-Regular.ttf", sys_fonts / "crosextra" / "Carlito-Bold.ttf",
         sys_fonts / "crosextra" / "Carlito-Italic.ttf", sys_fonts / "crosextra" / "Carlito-BoldItalic.ttf"),
        (dejavu / "DejaVuSans.ttf", dejavu / "DejaVuSans-Bold.ttf", dejavu / "DejaVuSans-Oblique.ttf", dejavu / "DejaVuSans-BoldOblique.ttf"),
    ]
    liberation = [
        (sys_fonts / "liberation" / "LiberationSans-Regular.ttf", sys_fonts / "liberation" / "LiberationSans-Bold.ttf",
         sys_fonts / "liberation" / "LiberationSans-Italic.ttf", sys_fonts / "liberation" / "LiberationSans-BoldItalic.ttf"),
    ] + carlito
    dejavu_serif = [
        (dejavu / "DejaVuSerif.ttf", dejavu / "DejaVuSerif-Bold.ttf", dejavu / "DejaVuSerif-Italic.ttf", dejavu / "DejaVuSerif-BoldItalic.ttf"),
    ]
    fam = (family or "").lower()
    if serif or fam in ("times", "times new roman", "georgia", "garamond"):
        return dejavu_serif + carlito
    if fam in ("arial", "helvetica", "liberation sans", "liberation"):
        return liberation + carlito
    return carlito


def pdf_fonts(family: str, serif: bool = False) -> dict:
    """Register a TTF family (Calibri metrics via Carlito when available) and return its names."""
    key = ("serif" if serif else "sans") + ":" + (family or "calibri").lower()
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    fallback = {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique",
                "bolditalic": "Helvetica-BoldOblique", "family": "Helvetica"}
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        raise ConnectorError("reportlab is not installed on the server.")
    for i, paths in enumerate(_font_paths(family, serif)):
        if not all(p.is_file() for p in paths):
            continue
        tag = re.sub(r"[^A-Za-z0-9]", "", key)[:20] + str(i)
        names = (f"ZB{tag}R", f"ZB{tag}B", f"ZB{tag}I", f"ZB{tag}BI")
        try:
            for name, path in zip(names, paths):
                pdfmetrics.registerFont(TTFont(name, str(path)))
            pdfmetrics.registerFontFamily(f"ZB{tag}", normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3])
            out = {"regular": names[0], "bold": names[1], "italic": names[2], "bolditalic": names[3],
                   "family": f"ZB{tag}"}
            _FONT_CACHE[key] = out
            return out
        except Exception:
            continue
    _FONT_CACHE[key] = fallback
    return fallback


# ---------------------------------------------------------------- normalising a CV
def _entry(value, kind: str) -> dict:
    if isinstance(value, dict):
        return dict(value)
    return {"title" if kind == "exp" else "qualification": C.collapse(value)}


def normalise_cv(raw: dict, prof: dict, region: dict) -> dict:
    cv = C.deep_merge(prof, raw or {})
    exp = []
    for item in C.as_list(cv.get("experience")):
        e = _entry(item, "exp")
        exp.append({
            "title": C.collapse(e.get("title") or e.get("role") or e.get("position")),
            "company": C.collapse(e.get("company") or e.get("employer") or e.get("organisation") or e.get("organization")),
            "location": C.collapse(e.get("location") or e.get("city")),
            "start": e.get("start") or e.get("from") or "",
            "end": e.get("end") or e.get("to") or e.get("year") or "",
            "bullets": [C.collapse(b) for b in C.as_list(e.get("bullets") or e.get("achievements") or e.get("description")) if C.collapse(b)],
        })
    cv["experience"] = [e for e in exp if e["title"] or e["company"] or e["bullets"]]

    edu = []
    for item in C.as_list(cv.get("education")):
        e = _entry(item, "edu")
        edu.append({
            "qualification": C.collapse(e.get("qualification") or e.get("degree") or e.get("title") or e.get("course")),
            "institution": C.collapse(e.get("institution") or e.get("school") or e.get("university") or e.get("college")),
            "location": C.collapse(e.get("location")),
            "start": e.get("start") or "",
            "end": e.get("end") or e.get("year") or "",
            "grade": C.collapse(e.get("grade") or e.get("result") or e.get("class")),
            "details": C.collapse(e.get("details") or e.get("subjects")),
        })
    cv["education"] = [e for e in edu if e["qualification"] or e["institution"]]

    certs = []
    for item in C.as_list(cv.get("certifications")):
        e = _entry(item, "cert")
        certs.append({"name": C.collapse(e.get("name") or e.get("title") or e.get("certification")),
                      "issuer": C.collapse(e.get("issuer") or e.get("body") or e.get("institution")),
                      "year": e.get("year") or e.get("date") or "",
                      "url": e.get("url") or ""})
    cv["certifications"] = [c for c in certs if c["name"]]

    refs = []
    for item in C.as_list(cv.get("referees")):
        if isinstance(item, dict):
            refs.append({"name": C.collapse(item.get("name")), "title": C.collapse(item.get("title") or item.get("position")),
                         "company": C.collapse(item.get("company") or item.get("organisation")),
                         "phone": C.collapse(item.get("phone") or item.get("tel")),
                         "email": C.collapse(item.get("email"))})
        else:
            refs.append({"name": C.collapse(item), "title": "", "company": "", "phone": "", "email": ""})
    cv["referees"] = [r for r in refs if r["name"]]

    skills = cv.get("skills")
    if isinstance(skills, str):
        cv["skills"] = [x.strip() for x in re.split(r"[,;\n]", skills) if x.strip()]
    cv["languages"] = C.as_list(cv.get("languages"))
    cv["projects"] = [p if isinstance(p, dict) else {"name": C.collapse(p)} for p in C.as_list(cv.get("projects"))]
    cv["full_name"] = C.collapse(cv.get("full_name") or cv.get("name"))
    cv["headline"] = C.collapse(cv.get("headline") or cv.get("target_role") or cv.get("title"))
    cv["summary"] = C.collapse(cv.get("summary") or cv.get("profile") or cv.get("professional_summary"))
    return cv


def skill_lines(cv: dict) -> list[str]:
    sk = cv.get("skills")
    if isinstance(sk, dict):
        out = []
        for k, v in sk.items():
            vals = C.as_list(v)
            if vals:
                out.append(f"{k}: " + ", ".join(vals))
        return out
    vals = C.as_list(sk)
    if not vals:
        return []
    if len(vals) <= 8:
        return [", ".join(vals)]
    lines, chunk = [], []
    for v in vals:
        chunk.append(v)
        if len(chunk) >= 8:
            lines.append(", ".join(chunk))
            chunk = []
    if chunk:
        lines.append(", ".join(chunk))
    return lines


def personal_lines(cv: dict, region: dict) -> list[str]:
    if not region.get("personal"):
        return []
    out = []
    if region.get("nationality") and cv.get("nationality"):
        out.append(f"Nationality: {cv['nationality']}")
    if region.get("dob") and cv.get("date_of_birth"):
        out.append(f"Date of birth: {fmt_date(cv['date_of_birth'], region)}")
    if region.get("marital") and cv.get("marital_status"):
        out.append(f"Marital status: {cv['marital_status']}")
    if region.get("id_number") and cv.get("id_number"):
        out.append(f"ID number: {cv['id_number']}")
    if cv.get("drivers_licence"):
        out.append(f"Driver's licence: {cv['drivers_licence']}")
    if cv.get("work_authorization"):
        out.append(f"Work authorisation: {cv['work_authorization']}")
    if cv.get("languages"):
        out.append("Languages: " + ", ".join(cv["languages"]))
    return out


def cv_sections(cv: dict, region: dict, include_referees: bool | None = None) -> list[tuple[str, list[dict]]]:
    out: list[tuple[str, list[dict]]] = []
    if cv.get("summary"):
        out.append(("PROFESSIONAL SUMMARY", [{"kind": "para", "text": cv["summary"]}]))
    lines = skill_lines(cv)
    if lines:
        out.append(("CORE SKILLS", [{"kind": "para", "text": x} for x in lines]))
    if cv.get("experience"):
        items = []
        for e in cv["experience"]:
            sub = ", ".join(x for x in (e.get("company"), e.get("location")) if x)
            dates = _date_range(e.get("start"), e.get("end"), region)
            items.append({"kind": "entry", "title": e.get("title", ""), "subtitle": sub,
                          "meta": dates, "bullets": e.get("bullets") or []})
        out.append(("PROFESSIONAL EXPERIENCE", items))
    if cv.get("education"):
        items = []
        for e in cv["education"]:
            sub = ", ".join(x for x in (e.get("institution"), e.get("location")) if x)
            dates = _date_range(e.get("start"), e.get("end"), region)
            extra = " | ".join(x for x in (e.get("grade"), e.get("details")) if x)
            items.append({"kind": "entry", "title": e.get("qualification", ""), "subtitle": sub,
                          "meta": dates, "bullets": [extra] if extra else []})
        out.append(("EDUCATION", items))
    if cv.get("certifications"):
        items = []
        for c in cv["certifications"]:
            bits = [c["name"]]
            if c.get("issuer"):
                bits.append(c["issuer"])
            if c.get("year"):
                bits.append(str(c["year"]))
            items.append({"kind": "para", "text": " - ".join(bits)})
        out.append(("CERTIFICATIONS & TRAINING", items))
    if cv.get("projects"):
        items = []
        for p in cv["projects"]:
            items.append({"kind": "entry", "title": C.collapse(p.get("name")), "subtitle": C.collapse(p.get("tech") or ""),
                          "meta": C.collapse(p.get("url") or ""), "bullets": [C.collapse(x) for x in C.as_list(p.get("description") or p.get("bullets"))]})
        out.append(("PROJECTS", items))
    want_refs = (region.get("referees") == "full") if include_referees is None else include_referees
    if want_refs and cv.get("referees"):
        items = []
        for r in cv["referees"]:
            head = ", ".join(x for x in (r.get("name"), r.get("title")) if x)
            tail = " | ".join(x for x in (r.get("company"), r.get("phone"), r.get("email")) if x)
            items.append({"kind": "para", "text": head + ((" | " + tail) if tail else "")})
        out.append(("REFERENCES", items))
    return out


def cv_plain(cv: dict, region: dict) -> str:
    lines = [cv.get("full_name", ""), cv.get("headline", ""), cv.get("email", ""), cv.get("phone", ""), cv.get("location", "")]
    lines.extend(personal_lines(cv, region))
    for heading, items in cv_sections(cv, region):
        lines.append("")
        lines.append(heading)
        for it in items:
            if it["kind"] == "para":
                lines.append(it["text"])
            else:
                lines.append(" ".join(x for x in (it.get("title"), it.get("subtitle")) if x))
                if it.get("meta"):
                    lines.append(it["meta"])
                for b in it.get("bullets") or []:
                    lines.append("• " + b)
    return "\n".join(x for x in lines if x is not None)


# ---------------------------------------------------------------- PDF rendering
def _paper(name: str):
    from reportlab.lib.pagesizes import A4, LETTER
    return LETTER if str(name).lower() == "letter" else A4


def _cv_pdf(cv: dict, region: dict, opts: dict, out: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib.utils import ImageReader

    template = opts.get("template", "ats")
    accent = colors.HexColor(opts.get("accent") or ("#1F3864" if template == "modern" else "#000000"))
    F = pdf_fonts(opts.get("font") or region.get("font", "Calibri"), serif=(template == "classic"))
    base = ParagraphStyle("cvbase", fontName=F["regular"], fontSize=opts.get("font_size", 10.2), leading=13.6,
                          textColor=colors.HexColor("#111111"), spaceAfter=2)
    name_style = ParagraphStyle("cvname", parent=base, fontName=F["bold"], fontSize=20 if template != "classic" else 19,
                                leading=23, textColor=accent, spaceAfter=2,
                                alignment=TA_CENTER if template == "classic" else 0)
    head_style = ParagraphStyle("cvhead", parent=base, fontSize=10.6, leading=13, textColor=accent, spaceAfter=4,
                                alignment=TA_CENTER if template == "classic" else 0)
    contact_style = ParagraphStyle("cvcontact", parent=base, fontSize=9, leading=11.6,
                                   alignment=TA_CENTER if template == "classic" else 0, spaceAfter=2)
    section_style = ParagraphStyle("cvsection", parent=base, fontName=F["bold"], fontSize=10.4, leading=12,
                                   textColor=accent, spaceBefore=8, spaceAfter=3)
    entry_style = ParagraphStyle("cventry", parent=base, fontName=F["bold"], spaceBefore=3)
    meta_style = ParagraphStyle("cvmeta", parent=base, fontName=F["italic"], fontSize=9, leading=11.4,
                                textColor=colors.HexColor("#333333"), spaceAfter=2)
    bullet_style = ParagraphStyle("cvbullet", parent=base, fontName=F["regular"], bulletFontName=F["regular"],
                                  leftIndent=10, bulletIndent=0, spaceAfter=1.2)

    doc = SimpleDocTemplate(str(out), pagesize=_paper(region.get("paper", "A4")),
                            leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"{cv.get('full_name', 'CV')} - Curriculum Vitae", author=cv.get("full_name", ""),
                            subject=cv.get("headline", ""), creator="ZerotheBot Job Hunt")
    story = []

    contact = " | ".join(x for x in (cv.get("location"), cv.get("phone"), cv.get("email"),
                                     cv.get("linkedin"), cv.get("portfolio")) if x)
    name_block = [Paragraph(C.esc(cv.get("full_name") or "Your Name"), name_style)]
    if cv.get("headline"):
        name_block.append(Paragraph(C.esc(cv["headline"]), head_style))
    if contact:
        name_block.append(Paragraph(C.esc(contact), contact_style))

    photo = opts.get("photo_path")
    if opts.get("photo") and photo and Path(photo).is_file() and template != "ats":
        try:
            iw, ih = ImageReader(str(photo)).getSize()
            w = 26 * mm
            img = Image(str(photo), width=w, height=w * ih / max(iw, 1))
            tbl = Table([[name_block, img]], colWidths=[doc.width - 30 * mm, 30 * mm])
            tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
            story.append(tbl)
        except Exception:
            story.extend(name_block)
    else:
        story.extend(name_block)

    if template != "ats":
        story.append(HRFlowable(width="100%", thickness=1.1, color=accent, spaceBefore=5, spaceAfter=3))

    personal = personal_lines(cv, region)
    if personal:
        story.append(Paragraph(C.esc(" | ".join(personal)), contact_style))

    for heading, items in cv_sections(cv, region, opts.get("referees")):
        story.append(Paragraph(C.esc(heading), section_style))
        if template != "ats":
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#BFBFBF"), spaceAfter=3))
        for it in items:
            if it["kind"] == "para":
                story.append(Paragraph(C.esc(it["text"]), base))
            else:
                title = C.esc(it.get("title") or "")
                if title:
                    story.append(Paragraph(title, entry_style))
                sub = " | ".join(x for x in (it.get("subtitle"), it.get("meta")) if x)
                if sub:
                    story.append(Paragraph(C.esc(sub), meta_style))
                for b in it.get("bullets") or []:
                    story.append(Paragraph(C.esc(b), bullet_style, bulletText="-" if template == "ats" else "•"))
    doc.build(story)


# ---------------------------------------------------------------- DOCX rendering
def _docx_set_font(run, name: str, size: float | None = None, bold: bool | None = None,
                   italic: bool | None = None, color: str | None = None) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color:
        run.font.color.rgb = RGBColor.from_string(color.replace("#", ""))


def _docx_heading(doc, text: str, font: str, color: str = "000000"):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(9)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text.upper())
    _docx_set_font(run, font, 10.8, bold=True, color=color)
    ppr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "BFBFBF")
    pbdr.append(bottom)
    ppr.append(pbdr)
    return p


def _cv_docx(cv: dict, region: dict, opts: dict, out: Path) -> None:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Inches, Pt

    template = opts.get("template", "ats")
    font = opts.get("font") or region.get("font", "Calibri")
    accent = (opts.get("accent") or ("1F3864" if template == "modern" else "000000")).replace("#", "")
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = font
    normal.font.size = Pt(opts.get("font_size", 10.5))
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.05
    from docx.oxml.ns import qn as _qn
    rpr = normal.element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(_qn("w:eastAsia"), font)

    sec = doc.sections[0]
    if str(region.get("paper", "A4")).lower() == "letter":
        sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    else:
        sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.7)
    sec.top_margin = sec.bottom_margin = Cm(1.5)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(1)
    if template == "classic":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _docx_set_font(p.add_run(cv.get("full_name") or "Your Name"), font, 20, bold=True, color=accent)
    if cv.get("headline"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        if template == "classic":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _docx_set_font(p.add_run(cv["headline"]), font, 10.8, color=accent)
    contact = " | ".join(x for x in (cv.get("location"), cv.get("phone"), cv.get("email"),
                                     cv.get("linkedin"), cv.get("portfolio")) if x)
    if contact:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(3)
        if template == "classic":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _docx_set_font(p.add_run(contact), font, 9)
    personal = personal_lines(cv, region)
    if personal:
        p = doc.add_paragraph()
        _docx_set_font(p.add_run(" | ".join(personal)), font, 9)

    for heading, items in cv_sections(cv, region, opts.get("referees")):
        _docx_heading(doc, heading, font, accent)
        for it in items:
            if it["kind"] == "para":
                p = doc.add_paragraph()
                _docx_set_font(p.add_run(it["text"]), font, opts.get("font_size", 10.5))
            else:
                if it.get("title"):
                    p = doc.add_paragraph()
                    p.paragraph_format.space_before = Pt(4)
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.keep_with_next = True
                    _docx_set_font(p.add_run(it["title"]), font, 10.5, bold=True)
                sub = " | ".join(x for x in (it.get("subtitle"), it.get("meta")) if x)
                if sub:
                    p = doc.add_paragraph()
                    p.paragraph_format.space_after = Pt(2)
                    p.paragraph_format.keep_with_next = bool(it.get("bullets"))
                    _docx_set_font(p.add_run(sub), font, 9.4, italic=True, color="333333")
                for b in it.get("bullets") or []:
                    p = doc.add_paragraph(style="List Bullet")
                    p.paragraph_format.space_after = Pt(1)
                    _docx_set_font(p.add_run(b), font, opts.get("font_size", 10.5))
    doc.save(str(out))


# ---------------------------------------------------------------- render + save
def _formats(value: str) -> list[str]:
    v = (value or "pdf").lower()
    if v in ("both", "all"):
        return ["pdf", "docx"]
    return ["docx"] if v == "docx" else ["pdf"]


def render_cv(ctx, stem: str, cv: dict, region: dict, opts: dict) -> dict:
    folder = C.resolve(ctx, C.CV_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    stem = C.slug(stem, "cv")
    out = {"stem": stem, "files": [], "paths": {}}
    for fmt in _formats(opts.get("format", "pdf")):
        path = folder / f"{stem}.{fmt}"
        try:
            if fmt == "pdf":
                _cv_pdf(cv, region, opts, path)
            else:
                _cv_docx(cv, region, opts, path)
            out["paths"][fmt] = path
            out["files"].append(C.file_info(ctx, path))
        except Exception as e:
            raise ConnectorError(f"Could not render the {fmt.upper()}: {type(e).__name__}: {e}") from e
    meta_path = folder / f"{stem}.json"
    C.save_json(meta_path, {"cv": cv, "region": region["key"], "options": {k: v for k, v in opts.items() if k != "photo_path"},
                            "created": C.today_str()})
    out["paths"]["json"] = meta_path
    out["text"] = cv_plain(cv, region)
    return out


def _cv_for(ctx, a: dict) -> tuple[dict, dict, str]:
    prof = C.load_profile(ctx)
    region = region_profile(a.get("region") or prof.get("default_region") or "zw")
    raw = {}
    for key in ("full_name", "headline", "summary", "skills", "experience", "education", "certifications",
                "projects", "languages", "nationality", "date_of_birth", "gender", "id_number",
                "drivers_licence", "email", "phone", "location", "linkedin", "portfolio", "work_authorization",
                "marital_status", "target_role"):
        if a.get(key) not in (None, "", []):
            raw[key] = a[key]
    if a.get("referees_list") not in (None, "", []):
        raw["referees"] = a["referees_list"]
    if a.get("cv"):
        raw = C.deep_merge(raw, a["cv"] if isinstance(a["cv"], dict) else {})
    cv = normalise_cv(raw, prof, region)
    target = C.collapse(a.get("target_role") or cv.get("target_role") or cv.get("headline"))
    if target and not cv.get("headline"):
        cv["headline"] = target
    stem = a.get("file_name") or f"{cv.get('full_name') or 'cv'}-cv" + (f"-{target}" if target else f"-{region['key']}")
    return cv, region, stem


def create_cv(ctx, a):
    cv, region, stem = _cv_for(ctx, a)
    if not cv.get("full_name"):
        raise ConnectorError("Your profile has no full name. Run jobhunt_profile_save with full_name, email and phone first.")
    if not (cv.get("experience") or cv.get("summary") or cv.get("education")):
        raise ConnectorError("Nothing to put in the CV yet. Add experience/education/summary to the profile "
                             "(jobhunt_profile_save) or pass them to this tool.")
    opts = {"format": a.get("format", "pdf"), "template": a.get("template", "ats"),
            "font": a.get("font") or region.get("font"), "font_size": a.get("font_size", 10.2),
            "photo": bool(a.get("photo", region.get("photo"))), "photo_path": a.get("photo_path"),
            "accent": a.get("accent"),
            "referees": a.get("referees") if isinstance(a.get("referees"), bool) else None}
    out = render_cv(ctx, stem, cv, region, opts)
    names = ", ".join(p.name for p in out["paths"].values() if p.suffix in (".pdf", ".docx"))
    return ToolResult(f"Created {names} for {region['label']} ({opts['template']} template, {opts['font']}). "
                      f"Saved under {C.CV_DIR}/{out['stem']}.",
                      files=out["files"], path=C.rel(ctx, out["paths"].get("pdf") or out["paths"]["docx"]),
                      data=out["text"][:12000])


def tailor_cv(ctx, a):
    job = C.get_job(ctx, a.get("job_id") or "") if a.get("job_id") else None
    desc = C.collapse(a.get("job_description") or "")
    if not desc and a.get("job_url"):
        from zbjh_search import read_job, extract_keywords
        read_job(ctx, {"url": a["job_url"]})
        job = C.get_job(ctx, a["job_url"])
    if job and not desc:
        desc = job.get("description", "")
    if not desc:
        raise ConnectorError("Give job_id, job_url or job_description so I can tailor the CV.")
    from zbjh_search import extract_keywords, job_match_score
    prof = C.load_profile(ctx)
    base = prof
    if a.get("base_cv"):
        p = C.resolve(ctx, a["base_cv"])
        if p.suffix.lower() == ".json":
            base = (C.load_json(p, {}) or {}).get("cv") or prof
        else:
            raise ConnectorError("base_cv must be one of the .json files saved next to a generated CV.")
    region = region_profile(a.get("region") or (job or {}).get("region") or prof.get("default_region"))
    kw = extract_keywords(desc)
    wanted = list(dict.fromkeys([s for s in kw.get("skills", [])] + list(kw.get("top_terms", [])[:12])))
    title = C.collapse(a.get("target_role") or (job or {}).get("title") or "")
    company = C.collapse((job or {}).get("company") or "")

    cv = normalise_cv(dict(base), prof, region)
    if title:
        cv["headline"] = title
    have = [s for s in C.profile_skills(cv)]
    matched = [s for s in have if any(w.lower() in s.lower() or s.lower() in w.lower() for w in wanted)]
    rest = [s for s in have if s not in matched]
    cv["skills"] = matched + rest
    years = kw.get("years_experience") or ""
    cv["summary"] = _tailored_summary(cv, title, company, matched, years, region)
    cv["experience"] = _tailor_experience(cv.get("experience") or [], wanted)

    if str(a.get("use_ai", True)).lower() not in ("false", "0", "no"):
        prompt = (
            "Rewrite this candidate's CV for the job below. Keep every fact true; never invent employers, "
            "dates, qualifications or numbers. Write like a human career coach: plain words, active verbs, "
            "no AI clichés (no leverage, spearheaded, passionate, dynamic, results-driven, seamless, robust). "
            "Mirror the advert's real keywords. Return ONLY JSON with keys: summary (3 sentences), "
            "skills (array of max 16), experience (array of {title, bullets: array of max 5}).\n\n"
            f"JOB TITLE: {title}\nCOMPANY: {company}\nJOB ADVERT:\n{desc[:9000]}\n\n"
            f"BASE CV:\n{cv_plain(cv, region)[:9000]}"
        )
        ai = C.json_from(C.llm(ctx, "You are a meticulous CV writer. Output JSON only.", prompt, 1800))
        if ai.get("summary"):
            cv["summary"] = C.collapse(ai["summary"])
        if isinstance(ai.get("skills"), list) and ai["skills"]:
            cv["skills"] = [C.collapse(x) for x in ai["skills"] if C.collapse(x)]
        if isinstance(ai.get("experience"), list):
            by_title = {C.collapse(e.get("title", "")).lower(): e for e in ai["experience"] if isinstance(e, dict)}
            for e in cv["experience"]:
                hit = by_title.get(C.collapse(e.get("title", "")).lower())
                if hit and C.as_list(hit.get("bullets")):
                    e["bullets"] = [C.collapse(b) for b in C.as_list(hit["bullets"])][:5]

    stem = a.get("file_name") or f"{cv.get('full_name') or 'cv'}-cv" + (f"-{company}" if company else "") + f"-{region['key']}"
    opts = {"format": a.get("format", "pdf"), "template": a.get("template", "ats"),
            "font": a.get("font") or region.get("font"), "font_size": a.get("font_size", 10.2),
            "photo": bool(a.get("photo", region.get("photo"))), "photo_path": a.get("photo_path"),
            "accent": a.get("accent"),
            "referees": a.get("referees") if isinstance(a.get("referees"), bool) else None}
    out = render_cv(ctx, stem, cv, region, opts)
    score = job_match_score({"title": title, "description": desc, "location": (job or {}).get("location", ""),
                             "remote": (job or {}).get("remote")}, prof, title)
    if job:
        data = C.load_jobs(ctx)
        if job["id"] in data["jobs"]:
            docs = data["jobs"][job["id"]].get("documents") or {}
            docs["cv"] = C.rel(ctx, out["paths"].get("pdf") or out["paths"]["docx"])
            data["jobs"][job["id"]]["documents"] = docs
            data["jobs"][job["id"]]["status"] = "ready"
            C.save_jobs(ctx, data)
    return ToolResult(
        f"Tailored CV for {title or 'the role'}" + (f" at {company}" if company else "") + f" ({score}% keyword match). "
        f"Kept the facts, re-ordered skills and rewrote the summary and bullets in a human tone. "
        f"Files: {', '.join(p.name for p in out['paths'].values() if p.suffix in ('.pdf', '.docx'))}.",
        files=out["files"], path=C.rel(ctx, out["paths"].get("pdf") or out["paths"]["docx"]),
        data=cv_plain(cv, region)[:12000])


def _tailored_summary(cv: dict, title: str, company: str, matched: list[str], years: str, region: dict) -> str:
    name = cv.get("full_name") or "I"
    top = ", ".join(matched[:3]) if matched else ", ".join(C.profile_skills(cv)[:3])
    role = title or cv.get("headline") or "the role"
    where = f" at {company}" if company else ""
    y = f"{years} years of " if years else ""
    base = f"{y}hands-on experience" if years else "hands-on experience"
    return (f"{role} candidate with {base} in {top or 'the core duties of the role'}. "
            f"Known for turning day-to-day work into measurable results and for picking up new systems quickly. "
            f"Looking to bring that same practical approach to {company or 'your team'}.").replace("  ", " ")


def _tailor_experience(experience: list[dict], wanted: list[str]) -> list[dict]:
    wl = [w.lower() for w in wanted if len(w) > 2]
    out = []
    for e in experience:
        bullets = list(e.get("bullets") or [])
        if wl and bullets:
            def score(b):
                low = b.lower()
                return sum(1 for w in wl if w in low)
            ranked = sorted(enumerate(bullets), key=lambda p: (score(p[1]), -p[0]), reverse=True)
            keep = [b for i, b in ranked[:5]]
            keep.sort(key=lambda b: bullets.index(b))
            bullets = keep
        row = dict(e)
        row["bullets"] = bullets
        out.append(row)
    return out


# ---------------------------------------------------------------- cover letters
def _letter_body(ctx, cv: dict, job: dict, region: dict, tone: str, use_ai: bool) -> str:
    title = C.collapse(job.get("title") or "the advertised role")
    company = C.collapse(job.get("company") or "your organisation")
    from zbjh_search import extract_keywords
    kw = extract_keywords(job.get("description", ""))
    skills = kw.get("skills", [])[:4]
    exp = cv.get("experience") or []
    first = exp[0] if exp else {}
    achievement = C.collapse((first.get("bullets") or [""])[0])
    if use_ai:
        prompt = (
            "Write a cover letter body (no addresses, no date, no signature) of 3-4 short paragraphs, 220-300 words. "
            "Sound like a real person: contractions are fine, vary sentence length, no AI clichés (no 'I am writing to "
            "express', 'passionate', 'dynamic', 'leverage', 'spearheaded', 'results-driven', 'thrilled'). "
            "Use only facts from the CV. Mention two or three advert requirements the candidate genuinely meets. "
            "Match this tone: " + tone + ". Return plain text only, paragraphs separated by blank lines.\n\n"
            f"JOB: {title} at {company}\nADVERT:\n{job.get('description', '')[:7000]}\n\n"
            f"CANDIDATE CV:\n{cv_plain(cv, region)[:7000]}"
        )
        text = C.llm(ctx, "You write natural, specific cover letters that do not sound AI-generated.", prompt, 1200)
        if text:
            return text

    name = cv.get("full_name") or "I"
    opening = f"I'd like to apply for the {title} role at {company}."
    if kw.get("seniority") in ("senior", "lead", "manager", "head", "director"):
        opening += " The scope of the position lines up well with what I have been doing day to day."
    para2 = ""
    if first:
        para2 = f"Most recently I worked as {C.with_article(first.get('title') or 'a professional')}" + (f" at {first.get('company')}" if first.get("company") else "") + "."
        if achievement:
            para2 += " " + achievement.rstrip(".") + "."
    matched = ", ".join(skills[:3]) if skills else ", ".join(C.profile_skills(cv)[:3])
    para3 = (f"The advert asks for {matched or 'someone who can hit the ground running'}. "
             f"That is the core of my experience, and I am comfortable working with limited supervision while keeping "
             f"colleagues and clients updated.")
    para4 = (f"I have attached my CV" + (" and certified copies of my qualifications" if region.get("key") in ("zw", "za", "africa") else "")
             + f". I am available for an interview at your convenience"
             + (f" and can start {cv.get('available_from')}" if cv.get("available_from") else "") + ".")
    closing = "Thank you for considering my application. I look forward to hearing from you."
    return "\n\n".join([opening, para2, para3, para4, closing]).replace("\n\n\n", "\n\n")


def _letter_pdf(letter: dict, region: dict, opts: dict, out: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    F = pdf_fonts(opts.get("font") or region.get("font", "Calibri"))
    base = ParagraphStyle("lbase", fontName=F["regular"], fontSize=10.6, leading=15.2, spaceAfter=8)
    small = ParagraphStyle("lsmall", parent=base, fontSize=9.6, leading=12.6, spaceAfter=2)
    bold = ParagraphStyle("lbold", parent=base, fontName=F["bold"], spaceAfter=6)
    doc = SimpleDocTemplate(str(out), pagesize=_paper(region.get("paper", "A4")),
                            leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"{letter.get('name', '')} - Cover Letter", author=letter.get("name", ""))
    story = [Paragraph(C.esc(letter.get("name", "")), bold)]
    for line in letter.get("sender", []):
        story.append(Paragraph(C.esc(line), small))
    story.append(Spacer(1, 10))
    story.append(Paragraph(C.esc(letter.get("date", "")), small))
    story.append(Spacer(1, 8))
    for line in letter.get("recipient", []):
        story.append(Paragraph(C.esc(line), small))
    story.append(Spacer(1, 8))
    if letter.get("subject"):
        story.append(Paragraph(C.esc(letter["subject"]), bold))
    story.append(Paragraph(C.esc(letter.get("salutation", "Dear Hiring Manager,")), base))
    for para in letter.get("body", "").split("\n\n"):
        if para.strip():
            story.append(Paragraph(C.esc(para.strip()), base))
    story.append(Spacer(1, 6))
    story.append(Paragraph(C.esc(letter.get("closing", "Kind regards,")), base))
    story.append(Paragraph(C.esc(letter.get("name", "")), bold))
    doc.build(story)


def _letter_docx(letter: dict, region: dict, opts: dict, out: Path) -> None:
    from docx import Document
    from docx.shared import Cm, Pt
    font = opts.get("font") or region.get("font", "Calibri")
    doc = Document()
    doc.styles["Normal"].font.name = font
    doc.styles["Normal"].font.size = Pt(10.8)
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2)
    p = doc.add_paragraph()
    _docx_set_font(p.add_run(letter.get("name", "")), font, 13, bold=True)
    for line in letter.get("sender", []):
        p = doc.add_paragraph()
        _docx_set_font(p.add_run(line), font, 9.6)
    doc.add_paragraph()
    p = doc.add_paragraph()
    _docx_set_font(p.add_run(letter.get("date", "")), font, 9.6)
    for line in letter.get("recipient", []):
        p = doc.add_paragraph()
        _docx_set_font(p.add_run(line), font, 9.6)
    doc.add_paragraph()
    if letter.get("subject"):
        p = doc.add_paragraph()
        _docx_set_font(p.add_run(letter["subject"]), font, 10.8, bold=True)
    p = doc.add_paragraph()
    _docx_set_font(p.add_run(letter.get("salutation", "Dear Hiring Manager,")), font, 10.8)
    for para in letter.get("body", "").split("\n\n"):
        if para.strip():
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(7)
            _docx_set_font(p.add_run(para.strip()), font, 10.8)
    p = doc.add_paragraph()
    _docx_set_font(p.add_run(letter.get("closing", "Kind regards,")), font, 10.8)
    p = doc.add_paragraph()
    _docx_set_font(p.add_run(letter.get("name", "")), font, 10.8, bold=True)
    doc.save(str(out))


def create_cover_letter(ctx, a):
    job = C.get_job(ctx, a.get("job_id") or "") if a.get("job_id") else None
    if not job and a.get("job_url"):
        from zbjh_search import read_job
        read_job(ctx, {"url": a["job_url"]})
        job = C.get_job(ctx, a["job_url"])
    job = job or {"title": a.get("job_title", ""), "company": a.get("company", ""),
                  "location": a.get("job_location", ""), "description": a.get("job_description", "")}
    prof = C.load_profile(ctx)
    region = region_profile(a.get("region") or job.get("region") or prof.get("default_region"))
    cv = normalise_cv(a.get("cv") or {}, prof, region)
    if not cv.get("full_name"):
        raise ConnectorError("Add your full name and contact details with jobhunt_profile_save first.")
    tone = a.get("tone") or region.get("tone", "professional")
    body = str(a["body"]).strip() if a.get("body") else ""
    if not body:
        body = _letter_body(ctx, cv, job, region, tone, str(a.get("use_ai", True)).lower() not in ("false", "0", "no"))
    subject = a.get("subject") or f"Application for the {job.get('title') or 'advertised'} position"
    recipient = [x for x in (job.get("company"), job.get("location")) if x] or ["Hiring Manager"]
    letter = {
        "name": cv.get("full_name"), "date": fmt_date(C.today_str(), region),
        "sender": [x for x in (cv.get("location"), cv.get("phone"), cv.get("email"), cv.get("linkedin")) if x],
        "recipient": recipient, "subject": subject,
        "salutation": a.get("salutation") or "Dear Hiring Manager,",
        "body": body, "closing": "Yours sincerely," if tone in ("formal", "very-formal", "warm-formal") else "Kind regards,",
    }
    folder = C.resolve(ctx, C.LETTER_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    stem = C.slug(a.get("file_name") or f"{cv.get('full_name')}-cover" + (f"-{job.get('company')}" if job.get("company") else ""))
    files, paths = [], {}
    for fmt in _formats(a.get("format", "pdf")):
        path = folder / f"{stem}.{fmt}"
        if fmt == "pdf":
            _letter_pdf(letter, region, {"font": a.get("font") or region.get("font")}, path)
        else:
            _letter_docx(letter, region, {"font": a.get("font") or region.get("font")}, path)
        paths[fmt] = path
        files.append(C.file_info(ctx, path))
    (folder / f"{stem}.txt").write_text(f"{letter['date']}\n\n" + "\n".join(recipient) + f"\n\n{subject}\n\n"
                                        + letter["salutation"] + "\n\n" + body + f"\n\n{letter['closing']}\n{letter['name']}\n",
                                        encoding="utf-8")
    if job.get("id"):
        data = C.load_jobs(ctx)
        if job["id"] in data["jobs"]:
            docs = data["jobs"][job["id"]].get("documents") or {}
            docs["cover_letter"] = C.rel(ctx, paths.get("pdf") or paths["docx"])
            data["jobs"][job["id"]]["documents"] = docs
            C.save_jobs(ctx, data)
    return ToolResult(f"Cover letter written for {job.get('title') or 'the role'}"
                      + (f" at {job.get('company')}" if job.get("company") else "")
                      + f" ({region['label']} style, {tone} tone). Files: "
                      + ", ".join(p.name for p in paths.values()) + ".",
                      files=files, path=C.rel(ctx, paths.get("pdf") or paths["docx"]),
                      data=body)


# ---------------------------------------------------------------- editing + documents
def _walk_replace(obj, find: str, repl: str):
    if isinstance(obj, str):
        return obj.replace(find, repl)
    if isinstance(obj, list):
        return [_walk_replace(x, find, repl) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk_replace(v, find, repl) for k, v in obj.items()}
    return obj


def update_cv(ctx, a):
    target = (a.get("file") or a.get("cv") or "").strip()
    if not target:
        raise ConnectorError("Give the CV file (the .json, .pdf or .docx under shared/jobhunt/cvs/).")
    p = C.resolve(ctx, target)
    if p.suffix.lower() == ".json" or (not p.suffix and p.with_suffix(".json").is_file()):
        meta_path = p if p.suffix.lower() == ".json" else p.with_suffix(".json")
        meta = C.load_json(meta_path, None)
        if not meta:
            raise ConnectorError(f"No editable CV data at {target}.")
        cv = meta.get("cv") or {}
        region = region_profile(meta.get("region"))
        for change in a.get("changes") or []:
            if isinstance(change, dict) and change.get("find"):
                cv = _walk_replace(cv, str(change["find"]), str(change.get("replace", "")))
        if isinstance(a.get("set"), dict):
            cv = C.deep_merge(cv, a["set"])
        cv = normalise_cv(cv, C.load_profile(ctx), region)
        opts = dict(meta.get("options") or {})
        opts["format"] = a.get("format") or opts.get("format") or "pdf"
        out = render_cv(ctx, meta_path.stem, cv, region, opts)
        return ToolResult(f"Updated {meta_path.stem} and re-rendered "
                          + ", ".join(q.name for q in out["paths"].values() if q.suffix in (".pdf", ".docx")) + ".",
                          files=out["files"], path=C.rel(ctx, out["paths"].get("pdf") or out["paths"]["docx"]),
                          data=out["text"][:12000])
    if p.suffix.lower() == ".docx" and p.is_file():
        try:
            from docx import Document
        except ImportError:
            raise ConnectorError("python-docx is not installed on the server.")
        doc = Document(str(p))
        changes = [c for c in (a.get("changes") or []) if isinstance(c, dict) and c.get("find")]
        if not changes:
            raise ConnectorError("Give changes: [{find, replace}].")
        count = 0
        for para in doc.paragraphs:
            for run in para.runs:
                for c in changes:
                    if c["find"] in run.text:
                        run.text = run.text.replace(str(c["find"]), str(c.get("replace", "")))
                        count += 1
        doc.save(str(p))
        return ToolResult(f"Edited {p.name} ({count} replacement(s)).", files=[C.file_info(ctx, p)], path=C.rel(ctx, p))
    raise ConnectorError("Edit the .json next to a generated CV (so it can be re-rendered), or a .docx file.")


def list_documents(ctx, a):
    rows = []
    for folder in (C.CV_DIR, C.LETTER_DIR, C.CERT_DIR, C.APP_DIR):
        base = C.resolve(ctx, folder)
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix.lower() in (".pdf", ".docx", ".json", ".txt", ".jpg", ".jpeg", ".png"):
                rows.append({"path": C.rel(ctx, p), "name": p.name, "size": p.stat().st_size,
                             "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                             "folder": folder})
    if not rows:
        return "No documents yet. Create one with jobhunt_create_cv or jobhunt_create_cover_letter."
    lines = [f"{len(rows)} document(s):"]
    for r in rows:
        lines.append(f"{r['modified']}  {r['size']:>9,}  {r['path']}")
    return ToolResult("\n".join(lines), data=json.dumps(rows, ensure_ascii=False))


def delete_document(ctx, a):
    target = (a.get("file") or a.get("path") or "").strip()
    if not target:
        raise ConnectorError("Give the file to delete.")
    if not a.get("confirm"):
        raise ConnectorError("Set confirm=true to delete. The file goes to shared/jobhunt/trash/ so it can be recovered.")
    p = C.resolve(ctx, target)
    if not p.is_file():
        raise ConnectorError(f"No such file: {target}")
    if C.resolve(ctx, C.BASE) not in p.resolve().parents:
        raise ConnectorError("Only files inside shared/jobhunt/ can be deleted here.")
    trash = C.resolve(ctx, C.BASE + "/trash")
    trash.mkdir(parents=True, exist_ok=True)
    dest = C.unique_path(trash / p.name)
    shutil.move(str(p), str(dest))
    if p.suffix.lower() == ".pdf":
        for ext in (".docx", ".json"):
            side = p.with_suffix(ext)
            if side.is_file():
                shutil.move(str(side), str(C.unique_path(trash / side.name)))
    return ToolResult(f"Moved {p.name} to {C.rel(ctx, dest)} (recoverable).")
