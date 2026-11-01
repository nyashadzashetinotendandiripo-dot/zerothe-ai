"""Job Hunt 2.0 - complete job search, CV, cover letter and application suite.

The tools are grouped in sibling modules so this file stays a readable index:
    zbjh_common.py   workspace, profile, tracker, HTTP, browser, LLM helpers
    zbjh_search.py   job boards, search, advert reading, keyword analysis, ranking
    zbjh_cv.py       region-aware CVs and cover letters (PDF + Word), tailoring, edits
    zbjh_review.py   spellcheck, human-tone rewriting, pre-send review
    zbjh_apply.py    application packs, Gmail sending, online form autofill

Add a new board in zbjh_search.BOARDS / REGION_SOURCES, or a new tool in register()
below, and it is immediately available to every Bot that has plugin:jobhunt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zbjh_common as C
from zbjh_apply import apply_online, prepare_application, send_application
from zbjh_cv import (create_cover_letter, create_cv, delete_document, list_documents, region_profile,
                     tailor_cv, update_cv)
from zbjh_review import extract_keywords_tool, humanize_text, review_document, spellcheck
from zbjh_search import (BOARDS, REGION_SOURCES, analyze_job, list_jobs, rank_jobs, read_job,
                         save_job, search_jobs, update_job)

try:
    from core.tooling import ToolResult
except ImportError:  # pragma: no cover - standalone testing
    class ToolResult:
        def __init__(self, text="", data="", images=None, files=None, is_error=False,
                     url="", path="", untrusted=""):
            self.text, self.data = text, data
            self.images, self.files = images or [], files or []
            self.is_error, self.url, self.path, self.untrusted = is_error, url, path, untrusted


GUIDE = """Job Hunt - how to use it

1. PROFILE (once)
   jobhunt_profile_save with full_name, email, phone, location, country, headline, summary,
   skills, experience (title, company, location, start, end, bullets), education,
   certifications, referees, documents.certificates, preferences.titles and
   preferences.regions. Everything is stored in shared/jobhunt/profile.json.

2. SEARCH
   jobhunt_search_jobs(query, region, location). Regions: zw, za, africa, europe, uk, us,
   gulf, remote, global. Zimbabwe/South Africa/Africa boards come first by default.
   jobhunt_read_job(job_id) pulls the advert text.
   jobhunt_analyze_job(job_id) extracts must-haves, ATS keywords and application needs.
   jobhunt_rank_jobs() scores everything in the tracker against the profile.

3. DOCUMENTS
   jobhunt_tailor_cv(job_id) rewrites the summary/bullets for that advert and re-orders
   skills, then renders PDF (and Word on request) with Calibri-metric fonts, ATS-safe
   layout and the right regional conventions (photo, personal details, referees, paper).
   jobhunt_create_cv builds a fresh CV; jobhunt_create_cover_letter writes the letter;
   jobhunt_update_cv edits a generated CV and re-renders it.

4. CHECK BEFORE SENDING
   jobhunt_review_document(path, job_id) gives a 0-100 score, ATS checks, keyword coverage,
   spelling, tone and layout problems. jobhunt_humanize_text removes AI-sounding phrases.
   jobhunt_spellcheck checks any text or file.

5. APPLY
   jobhunt_prepare_application(job_id) writes the email body and attaches the CV, cover
   letter and certificates. jobhunt_send_application(pack) sends it through the Gmail
   connector (asks for approval first).
   jobhunt_apply_online(url) fills an online application form in the Bot's browser,
   uploads the documents and stops before submit unless you approve it.

Files live in shared/jobhunt/ and every generated PDF/Word file is attached to the chat.
"""


def _t(name, description, props, required, handler, risk="safe", needs_screen=False):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required},
            "handler": handler, "risk": risk, "needs_screen": needs_screen}


def _s(desc):
    return {"type": "string", "description": desc}


def _b(desc):
    return {"type": "boolean", "description": desc}


def _i(desc):
    return {"type": "integer", "description": desc}


def _a(desc, items=None):
    return {"type": "array", "description": desc, "items": items or {"type": "string"}}


def _o(desc):
    return {"type": "object", "description": desc, "additionalProperties": True}


PROFILE_PROPS = {
    "full_name": _s("Full name as it should appear on the CV"),
    "headline": _s("Professional headline, e.g. 'Chartered Accountant | Audit & Reporting'"),
    "email": _s("Professional email address"),
    "phone": _s("Phone with country code, e.g. +263 77 123 4567"),
    "location": _s("City and country, e.g. Harare, Zimbabwe"),
    "address": _s("Street/postal address if the region expects one"),
    "country": _s("Country code or name (ZW, ZA, UK, DE, US...)"),
    "linkedin": _s("LinkedIn URL"),
    "portfolio": _s("Portfolio or personal website"),
    "github": _s("GitHub URL"),
    "nationality": _s("Nationality (used in ZW/ZA/Africa/Gulf/Germany CVs)"),
    "date_of_birth": _s("Date of birth YYYY-MM-DD (only where the region expects it)"),
    "gender": _s("Gender (only where the region expects it)"),
    "id_number": _s("National ID or passport number (only where expected)"),
    "drivers_licence": _s("Driver's licence class/code"),
    "marital_status": _s("Marital status (Gulf/Germany CVs only)"),
    "languages": _a("Languages, e.g. ['English (fluent)', 'Shona (native)']"),
    "summary": _s("3-sentence professional summary in your own voice"),
    "skills": _a("Skills: plain list, or grouped object like {'Technical': ['Excel','SQL']}"),
    "experience": _a("Work history: [{title, company, location, start, end, bullets:[...]}]", _o("One role")),
    "education": _a("Education: [{qualification, institution, location, start, end, grade}]", _o("One qualification")),
    "certifications": _a("Certifications: [{name, issuer, year}]", _o("One certificate")),
    "projects": _a("Projects: [{name, description, url}]", _o("One project")),
    "referees": _a("Referees: [{name, title, company, phone, email}]", _o("One referee")),
    "documents": _o("Document paths: {certificates: [...], transcripts: [...], other: [...]}"),
    "preferences": _o("Job preferences: {titles: [...], locations: [...], regions: [...], remote: true, min_salary: ''}"),
    "default_region": _s("Default CV region: zw, za, africa, uk, europe, de, fr, us, au, gulf"),
    "writing_style": _s("How you like to sound, e.g. 'plain, warm, direct'"),
    "work_authorization": _s("Work authorisation/visa status"),
    "notice_period": _s("Notice period"),
    "available_from": _s("Earliest start date"),
    "profile": _o("A whole profile object to merge (advanced)"),
}


def profile_get(ctx, a):
    prof = C.load_profile(ctx)
    missing = [k for k in ("full_name", "email", "phone", "location", "summary", "skills", "experience", "education")
               if not prof.get(k)]
    region = region_profile(prof.get("default_region"))
    lines = [f"Profile for {prof.get('full_name') or '(no name yet)'} - {prof.get('headline') or 'no headline'}",
             f"Contact: {prof.get('email') or 'no email'} | {prof.get('phone') or 'no phone'} | {prof.get('location') or 'no location'}",
             f"Default region: {region['label']} ({region['key']}) | font {region['font']} | "
             f"referees: {region['referees']}",
             f"Skills ({len(C.profile_skills(prof))}): " + (", ".join(C.profile_skills(prof)[:24]) or "none"),
             f"Roles: {len(prof.get('experience') or [])} | Education: {len(prof.get('education') or [])} | "
             f"Certificates: {len((prof.get('documents') or {}).get('certificates') or [])}",
             f"Preferences: {json.dumps(prof.get('preferences') or {}, ensure_ascii=False)}"]
    if missing:
        lines.append("Still missing: " + ", ".join(missing) + ". Use jobhunt_profile_save to add them.")
    else:
        lines.append("Profile is complete enough to build a CV.")
    return ToolResult("\n".join(lines), data=json.dumps(prof, ensure_ascii=False, indent=2))


def profile_save(ctx, a):
    patch = {k: v for k, v in (a.get("profile") or {}).items()} if isinstance(a.get("profile"), dict) else {}
    for key in PROFILE_PROPS:
        if key in a and key != "profile" and a[key] not in (None, ""):
            patch[key] = a[key]
    if not patch:
        raise C.ConnectorError("Give at least one profile field to save.")
    prof = C.save_profile(ctx, patch)
    missing = [k for k in ("full_name", "email", "phone", "location", "summary", "skills", "experience", "education")
               if not prof.get(k)]
    msg = f"Saved your profile ({len(patch)} field(s) updated) to {C.PROFILE_PATH}."
    if missing:
        msg += " Still missing: " + ", ".join(missing) + "."
    else:
        msg += " It is ready for jobhunt_create_cv / jobhunt_tailor_cv."
    return ToolResult(msg, data=json.dumps(prof, ensure_ascii=False, indent=2))


def boards(ctx, a):
    lines = ["Job boards by region (defaults, in priority order):"]
    for region, srcs in REGION_SOURCES.items():
        names = []
        for s in srcs:
            names.append(BOARDS.get(s, {}).get("name", s.title()))
        lines.append(f"  {region:8} " + ", ".join(names))
    lines.append("")
    lines.append("Company ATS boards (pass companies=['lever:shopify','greenhouse:stripe'] or sources=['lever','greenhouse']):")
    lines.append("  lever, greenhouse, workable, ashby, smartrecruiters")
    lines.append("Remote APIs: remotive, arbeitnow, jobicy, himalayas, remoteok, weworkremotely")
    lines.append("Add more in plugins/jobhunt/zbjh_search.py -> BOARDS / API_ADAPTERS.")
    return ToolResult("\n".join(lines))


def guide(ctx, a):
    return GUIDE


def register(api):
    C.set_api(api)
    return [
        _t("guide", "Read this first: how the Job Hunt suite works and the order to use the tools.",
           {}, [], guide),

        _t("profile_get", "Show the saved job-hunt profile, what is missing, and the default CV region.",
           {}, [], profile_get),

        _t("profile_save", "Create or update your master profile (contact details, experience, education, "
                           "skills, referees, certificates and job preferences). Lists are replaced when you pass "
                           "them; nested objects are merged. This profile feeds every CV and cover letter.",
           PROFILE_PROPS, [], profile_save, risk="write"),

        _t("boards", "List the supported job boards and which region each one is used for.",
           {}, [], boards),

        _t("search_jobs",
           "Search many job boards at once. Defaults to Zimbabwe, then South Africa, Africa, Europe, "
           "remote/global and LinkedIn depending on region. Reads public APIs and HTML boards, de-duplicates, "
           "scores each advert against your profile and saves results in the job tracker. Returns compact "
           "rows with tracker ids; use jobhunt_read_job next.",
           {"query": _s("Job title or keywords, e.g. 'accountant', 'software engineer', 'sales manager'"),
            "region": _s("zw, za, africa, europe, uk, us, gulf, remote or global (default: your profile region)"),
            "location": _s("City/country to search near, e.g. 'Harare', 'Johannesburg', 'Remote'"),
            "sources": _a("Limit to specific boards, e.g. ['linkedin','vacancymail','careerjunction']"),
            "companies": _a("Company ATS slugs, e.g. ['lever:shopify','greenhouse:stripe']"),
            "limit": _i("Max results (default 20, max 100)"),
            "use_browser": _s("auto (default), always or never - use the Bot browser for JS/anti-bot sites")},
           [], search_jobs),

        _t("read_job",
           "Open a job advert and save its clean text plus title, company, location, salary, dates and apply URL "
           "into the tracker.",
           {"job_id": _s("Tracker id from jobhunt_search_jobs"),
            "url": _s("Advert URL (alternative to job_id)"),
            "use_browser": _s("auto (default), always or never")},
           [], read_job),

        _t("analyze_job",
           "Analyse an advert: must-have skills, ATS keywords, seniority, years, qualifications, application "
           "requirements (cover letter, certificates, references), red flags, gaps and a match score. Uses the "
           "Bot's model plus deterministic keyword extraction; saves the analysis on the job.",
           {"job_id": _s("Tracker id"), "url": _s("Advert URL"), "job_description": _s("Paste the advert text instead"),
            "use_ai": _b("Use the model for a deeper analysis (default true)")},
           [], analyze_job),

        _t("rank_jobs",
           "Score every job in the tracker against your profile and list the best fits first.",
           {"status": _s("Only this status, e.g. saved, ready, applied"),
            "limit": _i("How many to show (default 20)")},
           [], rank_jobs),

        _t("save_job", "Add a job advert to the tracker manually (title, company and url required).",
           {"title": _s("Job title"), "company": _s("Company"), "url": _s("Application/advert URL"),
            "location": _s("Location"), "source": _s("Where you saw it"), "salary": _s("Salary"),
            "notes": _s("Your notes"), "status": _s("saved, shortlisted, ready, applied, interview, offer, rejected"),
            "remote": _b("Remote role"), "description": _s("Advert text")},
           ["title", "url"], save_job, risk="write"),

        _t("list_jobs", "List the job tracker, newest first.",
           {"status": _s("Filter by status"), "query": _s("Search text"), "limit": _i("Max rows (default 30)")},
           [], list_jobs),

        _t("update_job", "Update a tracked job: status, notes, applied date, documents or details.",
           {"job_id": _s("Tracker id or URL"), "status": _s("saved, shortlisted, ready, applied, interview, offer, rejected"),
            "notes": _s("Notes"), "applied_at": _s("YYYY-MM-DD"), "remind_at": _s("YYYY-MM-DD"),
            "title": _s("Title"), "company": _s("Company"), "location": _s("Location"), "salary": _s("Salary"),
            "documents": _o("Document paths, e.g. {cv, cover_letter, email_pack}")},
           ["job_id"], update_job, risk="write"),

        _t("extract_keywords",
           "Pull skills, soft skills, qualifications, seniority, years, application requirements, red flags and "
           "the top ATS terms out of any advert text.",
           {"text": _s("Advert text"), "job_id": _s("Tracker id"), "url": _s("Advert URL")},
           [], extract_keywords_tool),

        _t("create_cv",
           "Create an ATS-friendly CV as PDF (and Word on request). Uses Calibri-metric fonts, standard headings, "
           "single-column layout, and regional conventions (paper size, photo, personal details, referees). "
           "Saves the structured CV JSON so it can be edited and re-rendered later.",
           {"file_name": _s("File stem, e.g. 'tendai-moyo-cv'"),
            "region": _s("zw, za, africa, uk, europe, de, fr, us, au, gulf"),
            "target_role": _s("Role you are targeting (becomes the headline)"),
            "format": _s("pdf (default), docx or both"),
            "template": _s("ats (default, safest), modern or classic"),
            "font": _s("Font name, default Calibri (Carlito metrics are bundled)"),
            "font_size": {"type": "number", "description": "Body font size, default 10.2"},
            "photo": _b("Include a photo where the region expects one"),
            "photo_path": _s("Workspace path to a portrait photo"),
            "referees": _b("Include the referees section (default follows the region)"),
            "accent": _s("Hex accent colour for the modern template, e.g. #1F3864"),
            "full_name": _s("Override the profile name"), "headline": _s("Override the headline"),
            "summary": _s("Override the summary"), "skills": _a("Override skills"),
            "experience": _a("Override experience", _o("Role")),
            "education": _a("Override education", _o("Qualification")),
            "certifications": _a("Override certifications", _o("Certificate")),
            "referees_list": _a("Override referees", _o("Referee")),
            "cv": _o("Full CV object for advanced overrides")},
           [], create_cv, risk="write"),

        _t("tailor_cv",
           "Tailor the CV to one advert: mirrors the advert's real keywords, re-orders skills, keeps the most "
           "relevant bullets and rewrites the summary in a human tone. Never invents facts. Renders PDF/Word and "
           "links the file to the tracked job.",
           {"job_id": _s("Tracker id"), "job_url": _s("Advert URL"), "job_description": _s("Advert text"),
            "base_cv": _s("Optional .json CV to start from (default: your profile)"),
            "region": _s("Region override"), "target_role": _s("Role title override"),
            "use_ai": _b("Let the model rewrite the summary/bullets (default true)"),
            "format": _s("pdf (default), docx or both"), "template": _s("ats, modern or classic"),
            "font": _s("Font name"), "file_name": _s("File stem"),
            "photo": _b("Include a photo"), "photo_path": _s("Portrait photo path")},
           [], tailor_cv, risk="write"),

        _t("create_cover_letter",
           "Write a cover letter for one advert in a natural, human voice, with the right regional tone and "
           "salutation, and render it as PDF/Word.",
           {"job_id": _s("Tracker id"), "job_url": _s("Advert URL"), "job_title": _s("Role title"),
            "company": _s("Company"), "job_location": _s("Company location"), "job_description": _s("Advert text"),
            "region": _s("Region override"), "tone": _s("professional, warm-formal, formal, very-formal or direct"),
            "body": _s("Use this body text instead of generating one"), "subject": _s("Subject line"),
            "salutation": _s("e.g. 'Dear Ms Dube,'"), "use_ai": _b("Use the model (default true)"),
            "format": _s("pdf (default), docx or both"), "font": _s("Font name"), "file_name": _s("File stem")},
           [], create_cover_letter, risk="write"),

        _t("update_cv",
           "Edit a generated CV and re-render it. Best on the .json saved next to a CV (changes are applied to "
           "the structured data and PDF/Word are rebuilt); also does find/replace in a .docx.",
           {"file": _s("Path to the CV .json (or .docx)"),
            "changes": _a("Replacements: [{find, replace}]", _o("find/replace")),
            "set": _o("Fields to merge into the CV, e.g. {summary: '...'}"),
            "format": _s("pdf, docx or both")},
           ["file"], update_cv, risk="write"),

        _t("list_documents", "List the CVs, cover letters, certificates and application packs in shared/jobhunt/.",
           {}, [], list_documents),

        _t("delete_document", "Move a Job Hunt document to shared/jobhunt/trash/ (recoverable). Requires confirm=true.",
           {"file": _s("Path to delete"), "confirm": _b("Set true to confirm")},
           ["file", "confirm"], delete_document, risk="delete"),

        _t("review_document",
           "Pre-send review of a CV/cover letter: 0-100 score, ATS checks (tables, images, headings, fonts), "
           "advert keyword coverage, spelling, AI-sounding language, weak bullets, missing numbers, region "
           "conventions and contact details. Can apply spelling fixes and re-render the CV.",
           {"path": _s("PDF, DOCX or TXT path"),
            "job_id": _s("Compare against this tracked job"), "job_url": _s("Advert URL"),
            "job_description": _s("Advert text"), "region": _s("Region override"),
            "apply_fixes": _b("Apply spelling fixes and re-render when possible"),
            "context": _s("Extra names/terms the spellchecker should ignore")},
           ["path"], review_document),

        _t("spellcheck", "Spellcheck any text or workspace document and list suggestions.",
           {"text": _s("Text to check"), "path": _s("File to check instead"), "fix": _b("Save a corrected copy"),
            "context": _s("Names/terms to ignore")},
           [], spellcheck),

        _t("humanize_text",
           "Rewrite CV/cover-letter text so it sounds like a person wrote it: removes AI clichés, tightens "
           "phrasing, adds natural contractions. Keeps all facts. Optional model rewrite pass.",
           {"text": _s("Text to rewrite"), "path": _s("File to rewrite instead"),
            "rewrite": _b("Also run a full model rewrite"), "contractions": _b("Add natural contractions (default true)"),
            "style": _s("Desired style, e.g. 'plain, warm, direct'")},
           [], humanize_text),

        _t("prepare_application",
           "Build the application email: recipient (from the advert when possible), subject, a human-sounding "
           "body, and the CV + cover letter + certificates as attachments. Saves a pack under "
           "shared/jobhunt/applications/. Review it, then call jobhunt_send_application.",
           {"job_id": _s("Tracker id"), "job_url": _s("Advert URL"), "job_title": _s("Role title"),
            "company": _s("Company"), "to": _s("Recipient email (overrides auto-detect)"),
            "cc": _s("Cc"), "subject": _s("Subject line"), "body": _s("Email body override"),
            "cv": _s("CV path (default: newest / the one linked to the job)"),
            "cover_letter": _s("Cover letter path"), "certificates": _a("Certificate paths to attach"),
            "include_certificates": _b("Attach certificates from the profile (default true)"),
            "region": _s("Region override"), "use_ai": _b("Use the model for the body (default true)")},
           [], prepare_application, risk="write"),

        _t("send_application",
           "Send an application pack through the Gmail connector with the CV/cover letter/certificates attached. "
           "Always asks for approval before sending.",
           {"pack": _s("Path to the pack .json"), "job_id": _s("Or a job id with an existing pack"),
            "to": _s("Recipient override"), "cc": _s("Cc"), "subject": _s("Subject override"),
            "body": _s("Body override"), "attachments": _a("Attachment paths override")},
           [], send_application, risk="send"),

        _t("apply_online",
           "Fill an online application form in the Bot's browser: matches labels/names to your profile, uploads "
           "the CV/cover letter/certificates, and reports what it filled. Dry run by default; set submit=true "
           "(after approval) to press the final apply button.",
           {"url": _s("Application form URL"), "job_id": _s("Tracker id"), "job_url": _s("Job URL"),
            "cv": _s("CV path"), "cover_letter": _s("Cover letter path"), "certificates": _a("Certificate paths"),
            "include_certificates": _b("Upload profile certificates too"),
            "answers": _o("Extra answers keyed by field name, e.g. {salary: 'USD 1200'}"),
            "cover_letter_text": _s("Text to put in a cover-letter/message box"),
            "dry_run": _b("Fill only, do not submit (default true)"),
            "submit": _b("Press the final apply/submit button (requires dry_run=false and approval)"),
            "wait_ms": _i("Extra wait after page load (default 2500)"), "region": _s("Region override")},
           [], apply_online, risk="submit", needs_screen=True),
    ]
