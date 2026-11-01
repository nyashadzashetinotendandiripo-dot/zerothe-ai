"""Job Hunt: application packs, Gmail sending and online form autofill."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import zbjh_common as C
from zbjh_common import ConnectorError, ToolResult

SCAN_JS = r"""
() => {
  const out = [];
  const cssEsc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  const sel = (el) => {
    if (el.id) return "#" + cssEsc(el.id);
    if (el.name) return el.tagName.toLowerCase() + '[name="' + String(el.name).replace(/"/g, '\\"') + '"]';
    let i = 0, n = el;
    while ((n = n.previousElementSibling)) if (n.tagName === el.tagName) i++;
    return el.tagName.toLowerCase() + ":nth-of-type(" + (i + 1) + ")";
  };
  const labelOf = (el) => {
    let t = "";
    try { if (el.labels && el.labels.length) t = Array.from(el.labels).map(l => l.innerText).join(" "); } catch (e) {}
    if (!t && el.getAttribute && el.getAttribute("aria-label")) t = el.getAttribute("aria-label");
    if (!t && el.id) { const l = document.querySelector('label[for="' + cssEsc(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t) { const p = el.closest("label, .field, .form-group, .question, fieldset, div"); if (p) t = p.innerText || ""; }
    return (t || "").replace(/\s+/g, " ").trim().slice(0, 200);
  };
  document.querySelectorAll("input, textarea, select").forEach((el) => {
    const type = (el.type || el.tagName).toLowerCase();
    if (["hidden", "submit", "button", "reset", "image", "search"].includes(type)) return;
    if (el.disabled || el.readOnly) return;
    out.push({sel: sel(el), tag: el.tagName.toLowerCase(), type: type, name: el.name || "", id: el.id || "",
              placeholder: el.placeholder || "", label: labelOf(el), required: !!el.required,
              options: el.tagName === "SELECT" ? Array.from(el.options).map(o => ({value: o.value, text: o.text})) : []});
  });
  return out;
}
"""

SETTER_JS = r"""
({sel, value}) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  try {
    const d = Object.getOwnPropertyDescriptor(el.__proto__, "value");
    if (d && d.set) d.set.call(el, value); else el.value = value;
  } catch (e) { el.value = value; }
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  el.dispatchEvent(new Event("blur", {bubbles: true}));
  return true;
}
"""

SUBMIT_JS = r"""
() => {
  const els = Array.from(document.querySelectorAll("button, input[type=submit], a[role=button], a"));
  const re = /(submit|send application|apply now|finish application|complete application)/i;
  const bad = /(save|cancel|back|previous|draft|delete|remove)/i;
  const el = els.find(x => re.test((x.innerText || x.value || "").trim()) && !bad.test((x.innerText || x.value || "")) && !x.disabled);
  if (!el) return null;
  el.setAttribute("data-zb-submit", "1");
  return '[data-zb-submit="1"]';
}
"""

FIELD_PATTERNS = [
    ("first_name", [r"\bfirst\b", r"given name", r"forename"]),
    ("last_name", [r"\blast\b", r"surname", r"family name"]),
    ("email", [r"e-?mail"]),
    ("phone", [r"phone", r"mobile", r"cell", r"telephone", r"contact number"]),
    ("full_name", [r"full name", r"your name", r"^name$", r"\bname\b"]),
    ("address", [r"street", r"physical address", r"residential address", r"\baddress\b"]),
    ("city", [r"\bcity\b", r"\btown\b", r"current location", r"\blocation\b"]),
    ("postal", [r"postal", r"\bzip\b", r"post code", r"postcode"]),
    ("country", [r"country"]),
    ("linkedin", [r"linkedin"]),
    ("website", [r"website", r"portfolio", r"github", r"personal site"]),
    ("summary", [r"cover letter", r"motivation", r"message", r"about you", r"why do you", r"summary", r"additional information"]),
    ("current_company", [r"current (company|employer)", r"present employer", r"organisation", r"organization"]),
    ("current_title", [r"current (job )?title", r"job title", r"position", r"\brole\b"]),
    ("years_experience", [r"years? of experience", r"experience \(years\)", r"total experience"]),
    ("salary", [r"salary", r"remuneration", r"expected pay", r"expected rate", r"pay expectation"]),
    ("availability", [r"available", r"availability", r"notice period", r"start date", r"when can you start"]),
    ("nationality", [r"nationality", r"citizenship"]),
    ("dob", [r"date of birth", r"birth date", r"\bdob\b"]),
    ("gender", [r"gender", r"\bsex\b"]),
    ("id_number", [r"id number", r"identity number", r"national id", r"passport number"]),
    ("drivers_licence", [r"driver", r"driving licence", r"driving license"]),
    ("qualification", [r"highest (qualification|education)", r"qualification", r"degree"]),
    ("institution", [r"institution", r"university", r"school", r"college"]),
    ("referee", [r"referee", r"reference"]),
    ("source", [r"how did you (hear|find)", r"\bsource\b", r"referred by", r"referral"]),
]

CONSENT_RE = re.compile(r"(agree|consent|terms|privacy|confirm|declare|certify)", re.I)

COUNTRY_NAMES = {"ZW": "Zimbabwe", "ZA": "South Africa", "KE": "Kenya", "NG": "Nigeria", "GH": "Ghana",
                 "ZM": "Zambia", "BW": "Botswana", "NA": "Namibia", "MZ": "Mozambique", "TZ": "Tanzania",
                 "UG": "Uganda", "MW": "Malawi", "UK": "United Kingdom", "GB": "United Kingdom",
                 "US": "United States", "CA": "Canada", "AU": "Australia", "NZ": "New Zealand",
                 "DE": "Germany", "FR": "France", "NL": "Netherlands", "ES": "Spain", "IT": "Italy",
                 "PT": "Portugal", "IE": "Ireland", "BE": "Belgium", "CH": "Switzerland", "AT": "Austria",
                 "AE": "United Arab Emirates", "SA": "Saudi Arabia", "QA": "Qatar"}


def _find_docs(ctx, job: dict, a: dict) -> tuple[str, str]:
    cv = a.get("cv") or ((job.get("documents") or {}).get("cv") if job else "")
    letter = a.get("cover_letter") or ((job.get("documents") or {}).get("cover_letter") if job else "")
    if not cv:
        folder = C.resolve(ctx, C.CV_DIR)
        if folder.exists():
            files = sorted([p for p in folder.iterdir() if p.suffix.lower() in (".pdf", ".docx")],
                           key=lambda p: p.stat().st_mtime, reverse=True)
            cv = C.rel(ctx, files[0]) if files else ""
    if not letter:
        folder = C.resolve(ctx, C.LETTER_DIR)
        if folder.exists():
            files = sorted([p for p in folder.iterdir() if p.suffix.lower() in (".pdf", ".docx")],
                           key=lambda p: p.stat().st_mtime, reverse=True)
            letter = C.rel(ctx, files[0]) if files else ""
    return cv, letter


def _certificates(ctx, prof: dict, extra: list[str], include: bool) -> list[str]:
    if not include and not extra:
        return []
    paths = list(extra)
    docs = prof.get("documents") or {}
    for key in ("certificates", "transcripts", "other"):
        paths.extend(C.as_list(docs.get(key)))
    out, seen = [], set()
    for rel_path in paths:
        p = C.resolve(ctx, rel_path)
        if p.is_file() and str(p) not in seen and p.stat().st_size <= 20_000_000:
            seen.add(str(p))
            out.append(C.rel(ctx, p))
    return out


def _email_body(ctx, prof: dict, job: dict, region, attachments: list[str], use_ai: bool) -> str:
    title = C.collapse(job.get("title") or "the advertised role")
    company = C.collapse(job.get("company") or "your organisation")
    source = C.collapse(job.get("source") or "")
    first = (prof.get("experience") or [{}])[0]
    highlight = ""
    if isinstance(first, dict) and first.get("bullets"):
        highlight = C.collapse(first["bullets"][0])
    if use_ai and job.get("description"):
        prompt = (
            "Write a short application email body, 110-160 words. Plain, human, specific. No AI clichés and no "
            "'I am writing to express'. Mention the role, one genuine achievement from the CV, and the attachments. "
            "End with availability and thanks. Return plain text only, paragraphs separated by blank lines.\n\n"
            f"ROLE: {title} at {company}\nADVERT:\n{job.get('description', '')[:5000]}\n\n"
            f"CANDIDATE:\n{C.profile_summary_text(prof)[:3000]}"
        )
        text = C.llm(ctx, "You write short, natural job application emails.", prompt, 700)
        if text:
            return text
    docs = []
    if any(x.lower().endswith((".pdf", ".docx")) for x in attachments):
        docs.append("my CV and cover letter")
    if any(re.search(r"cert|certif|transcript|qualif", x, re.I) for x in attachments):
        docs.append("copies of my qualifications")
    attached = (" I have attached " + " and ".join(docs) + ".") if docs else ""
    lines = [f"Dear Hiring Manager,",
             f"I'd like to apply for the {title} role at {company}"
             + (f", which I saw on {source}" if source else "") + ".",
             (highlight.rstrip(".") + ".") if highlight else "",
             f"My background lines up with what the advert asks for, and I'd be glad to talk through how I can help."
             + attached,
             "I am available for an interview at your convenience"
             + (f" and can start {prof.get('available_from')}" if prof.get("available_from") else "") + ".",
             "Thank you for your time and consideration.",
             "Kind regards,",
             prof.get("full_name") or "",
             " | ".join(x for x in (prof.get("phone"), prof.get("email")) if x)]
    return "\n\n".join(x for x in lines if x)


def prepare_application(ctx, a):
    job = C.get_job(ctx, a.get("job_id") or "") if a.get("job_id") else None
    if not job and a.get("job_url"):
        from zbjh_search import read_job
        read_job(ctx, {"url": a["job_url"]})
        job = C.get_job(ctx, a["job_url"])
    job = job or {"title": a.get("job_title", ""), "company": a.get("company", ""), "url": a.get("job_url", ""),
                  "description": a.get("job_description", ""), "source": a.get("source", "")}
    prof = C.load_profile(ctx)
    from zbjh_cv import region_profile
    region = region_profile(a.get("region") or (job or {}).get("region") or prof.get("default_region"))
    cv, letter = _find_docs(ctx, job, a)
    attachments = []
    for rel_path in [cv, letter] + _certificates(ctx, prof, C.as_list(a.get("certificates")), bool(a.get("include_certificates", True))):
        if rel_path and rel_path not in attachments:
            p = C.resolve(ctx, rel_path)
            if not p.is_file():
                raise ConnectorError(f"Attachment not found: {rel_path}")
            if p.stat().st_size > 20_000_000:
                raise ConnectorError(f"{rel_path} is over Gmail's 20 MB attachment limit.")
            attachments.append(C.rel(ctx, p))
    if not attachments:
        raise ConnectorError("No CV found. Run jobhunt_create_cv or jobhunt_tailor_cv first.")

    to = C.collapse(a.get("to") or a.get("email") or "")
    if not to and job.get("description"):
        found = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", job["description"])
        found = [x for x in found if not re.search(r"(example|noreply|no-reply|donotreply)", x, re.I)]
        to = found[0] if found else ""
    subject = C.collapse(a.get("subject") or f"Application: {job.get('title') or 'Role'} - {prof.get('full_name') or 'Candidate'}")
    body = str(a.get("body") or "").strip()
    if not body:
        body = _email_body(ctx, prof, job, region, attachments,
                           str(a.get("use_ai", True)).lower() not in ("false", "0", "no"))
    salutation = a.get("salutation") or "Dear Hiring Manager,"
    if not body.lstrip().lower().startswith(("dear", "hello", "hi")):
        body = salutation + "\n\n" + body
    if prof.get("full_name") and prof["full_name"].lower() not in body.lower():
        body = body.rstrip() + "\n\nKind regards,\n" + prof["full_name"] + "\n" + " | ".join(
            x for x in (prof.get("phone"), prof.get("email")) if x)

    pack = {"to": to, "cc": C.collapse(a.get("cc") or ""), "subject": subject, "body": body,
            "attachments": attachments, "job_id": (job or {}).get("id", ""), "job_url": job.get("url", ""),
            "company": job.get("company", ""), "role": job.get("title", ""), "created": C.today_str(),
            "region": region["key"], "status": "draft"}
    folder = C.resolve(ctx, C.APP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    stem = C.slug(f"{prof.get('full_name') or 'application'}-{job.get('company') or 'job'}-{job.get('title') or ''}")
    json_path = C.unique_path(folder / f"{stem}.json")
    C.save_json(json_path, pack)
    txt = (f"To: {to or '(add recipient)'}\nSubject: {subject}\n\n{body}\n\nAttachments:\n"
           + "\n".join("  - " + x for x in attachments))
    txt_path = json_path.with_suffix(".txt")
    txt_path.write_text(txt, encoding="utf-8")
    if job.get("id"):
        data = C.load_jobs(ctx)
        if job["id"] in data["jobs"]:
            docs = data["jobs"][job["id"]].get("documents") or {}
            docs["email_pack"] = C.rel(ctx, json_path)
            data["jobs"][job["id"]]["documents"] = docs
            if data["jobs"][job["id"]].get("status") in ("saved", "ready"):
                data["jobs"][job["id"]]["status"] = "ready"
            C.save_jobs(ctx, data)

    files = [C.file_info(ctx, C.resolve(ctx, x)) for x in attachments]
    lines = [f"Application pack ready for {job.get('title') or 'the role'}" + (f" at {job.get('company')}" if job.get("company") else ""),
             f"To: {to or '(no recipient found - pass to=)'}",
             f"Subject: {subject}",
             f"Attachments ({len(attachments)}): " + ", ".join(Path(x).name for x in attachments),
             f"Pack saved to {C.rel(ctx, json_path)}.",
             "Body:", body]
    if not to:
        lines.append("No recipient address was found. Pass to=... then call jobhunt_send_application.")
    else:
        lines.append("Next: review the body, then call jobhunt_send_application with this pack (it will ask for approval).")
    return ToolResult("\n".join(lines), data=json.dumps(pack, ensure_ascii=False, indent=2), files=files,
                      path=C.rel(ctx, json_path))


def _gmail_send(ctx, to: str, subject: str, body: str, attachments: list[str], cc: str = "") -> str:
    grants = set(ctx.bot.get("grants") or [])
    if "plugin:gmail" not in grants:
        raise ConnectorError("This Bot does not have the Gmail connector. Grant plugin:gmail in the Bot's "
                             "settings (Plugins tab), then try again.")
    specs = ctx.engine.plugins.tool_specs(grants)
    gm = next((s for s in specs if s.name == "gmail_send"), None)
    if gm is None:
        raise ConnectorError("Gmail is not configured yet. Open Plugins > Gmail and press Connect.")
    res = gm.handler(ctx, {"to": to, "subject": subject, "body": body, "attachments": attachments, "cc": cc})
    return getattr(res, "text", str(res))


def send_application(ctx, a):
    pack_path = (a.get("pack") or "").strip()
    pack = {}
    if pack_path:
        pack = C.load_json(C.resolve(ctx, pack_path), {}) or {}
    if a.get("job_id") and not pack:
        job = C.get_job(ctx, a["job_id"])
        if job and (job.get("documents") or {}).get("email_pack"):
            pack = C.load_json(C.resolve(ctx, job["documents"]["email_pack"]), {}) or {}
    if not pack:
        raise ConnectorError("Give the application pack path (shared/jobhunt/applications/...json) or a job_id "
                             "that already has a pack. Run jobhunt_prepare_application first.")
    to = C.collapse(a.get("to") or pack.get("to") or "")
    if not to:
        raise ConnectorError("The pack has no recipient. Pass to=email@company.com.")
    subject = C.collapse(a.get("subject") or pack.get("subject") or "Job application")
    body = str(a.get("body") or pack.get("body") or "")
    attachments = C.as_list(a.get("attachments") or pack.get("attachments"))
    for rel_path in attachments:
        if not C.resolve(ctx, rel_path).is_file():
            raise ConnectorError(f"Attachment missing: {rel_path}")
    result = _gmail_send(ctx, to, subject, body, attachments, C.collapse(a.get("cc") or pack.get("cc") or ""))
    if pack_path:
        p = C.resolve(ctx, pack_path)
        pack["status"] = "sent"
        pack["sent_at"] = C.today_str()
        C.save_json(p, pack)
    job_id = a.get("job_id") or pack.get("job_id")
    if job_id:
        data = C.load_jobs(ctx)
        if job_id in data["jobs"]:
            data["jobs"][job_id]["status"] = "applied"
            data["jobs"][job_id]["applied_at"] = C.today_str()
            C.save_jobs(ctx, data)
    return ToolResult(f"{result} To: {to}. Subject: {subject}. Attached: "
                      + (", ".join(Path(x).name for x in attachments) or "nothing") + ".")


# ---------------------------------------------------------------- online forms
def _form_values(prof: dict, job: dict, answers: dict) -> dict:
    exp = prof.get("experience") or [{}]
    first_exp = exp[0] if isinstance(exp[0], dict) else {}
    edu = prof.get("education") or [{}]
    first_edu = edu[0] if isinstance(edu[0], dict) else {}
    refs = prof.get("referees") or [{}]
    first_ref = refs[0] if isinstance(refs[0], dict) else {}
    name_parts = (prof.get("full_name") or "").split()
    country = C.collapse(prof.get("country_name") or prof.get("country") or "")
    if len(country) == 2:
        country = COUNTRY_NAMES.get(country.upper(), country)
    values = {
        "first_name": name_parts[0] if name_parts else "",
        "last_name": " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
        "full_name": prof.get("full_name") or "",
        "email": prof.get("email") or "",
        "phone": prof.get("phone") or "",
        "address": prof.get("address") or prof.get("location") or "",
        "city": prof.get("city") or prof.get("location") or "",
        "postal": prof.get("postal_code") or "",
        "country": country,
        "linkedin": prof.get("linkedin") or "",
        "website": prof.get("portfolio") or prof.get("github") or "",
        "summary": prof.get("summary") or "",
        "current_company": first_exp.get("company") or "",
        "current_title": first_exp.get("title") or "",
        "years_experience": str(prof.get("years_experience") or ""),
        "salary": str((prof.get("preferences") or {}).get("min_salary") or prof.get("salary_expectation") or ""),
        "availability": prof.get("available_from") or prof.get("notice_period") or "",
        "nationality": prof.get("nationality") or "",
        "dob": prof.get("date_of_birth") or "",
        "gender": prof.get("gender") or "",
        "id_number": prof.get("id_number") or "",
        "drivers_licence": prof.get("drivers_licence") or "",
        "qualification": first_edu.get("qualification") or "",
        "institution": first_edu.get("institution") or "",
        "referee": ", ".join(x for x in (first_ref.get("name"), first_ref.get("phone")) if x),
        "source": "LinkedIn" if "linkedin" in (job.get("source") or "").lower() else (job.get("source") or ""),
    }
    for k, v in (answers or {}).items():
        if k in values:
            values[k] = str(v)
        else:
            values[str(k)] = str(v)
    return {k: v for k, v in values.items() if v}


def _match(field: dict, values: dict) -> tuple[str, str]:
    blob = " ".join([field.get("label", ""), field.get("name", ""), field.get("id", ""),
                     field.get("placeholder", "")]).lower()
    if re.search(r"company name|employer name|username|user name", blob):
        return "", ""
    skip_full_name = bool(re.search(r"referee|reference|next of kin|emergency|guardian|school|institution", blob))
    for key, patterns in FIELD_PATTERNS:
        if key == "full_name" and skip_full_name:
            continue
        if key in values and any(re.search(p, blob) for p in patterns):
            return key, values[key]
    for key, val in values.items():
        if key not in {k for k, _ in FIELD_PATTERNS} and re.search(r"\b" + re.escape(key.replace("_", " ")) + r"\b", blob):
            return key, val
    return "", ""


def apply_online(ctx, a):
    url = C.collapse(a.get("url") or a.get("job_url") or "")
    job = C.get_job(ctx, a.get("job_id") or "") if a.get("job_id") else None
    if not url and job:
        url = job.get("apply_url") or job.get("url") or ""
    if not url:
        raise ConnectorError("Give the online application URL (or a job id with a URL).")
    prof = C.load_profile(ctx)
    from zbjh_cv import region_profile
    region = region_profile(a.get("region") or prof.get("default_region"))
    cv, letter = _find_docs(ctx, job or {}, a)
    certs = _certificates(ctx, prof, C.as_list(a.get("certificates")), bool(a.get("include_certificates", False)))
    values = _form_values(prof, job or {}, a.get("answers") or {})
    if a.get("cover_letter_text"):
        values["summary"] = str(a["cover_letter_text"])
    dry_run = str(a.get("dry_run", True)).lower() not in ("false", "0", "no")
    do_submit = bool(a.get("submit")) and not dry_run

    br = ctx.engine.computer.browser
    scr = br.screen(ctx.bot["id"])
    br.call(scr.goto, url)
    page = br.call(scr.ensure_page)
    try:
        br.call(page.wait_for_timeout, int(a.get("wait_ms", 2500)))
    except Exception:
        pass

    fields = br.call(page.evaluate, SCAN_JS)
    filled, failed, skipped, files_uploaded = [], [], [], []
    file_queue = [x for x in (cv, letter) + tuple(certs) if x]
    for f in fields or []:
        ftype = f.get("type", "")
        if ftype == "file":
            if file_queue:
                rel_path = file_queue.pop(0)
                abs_path = str(C.resolve(ctx, rel_path))
                try:
                    br.call(page.set_input_files, f["sel"], abs_path)
                    files_uploaded.append({"field": f.get("label") or f.get("name"), "file": rel_path})
                except Exception as e:
                    failed.append({"field": f.get("label") or f.get("name"), "reason": str(e)[:120]})
            continue
        if ftype in ("checkbox", "radio"):
            if do_submit and CONSENT_RE.search((f.get("label") or "") + " " + (f.get("name") or "")):
                try:
                    br.call(page.check, f["sel"])
                    filled.append({"field": f.get("label") or f.get("name"), "value": "checked"})
                except Exception:
                    pass
            continue
        key, val = _match(f, values)
        if not val:
            if f.get("required"):
                skipped.append({"field": f.get("label") or f.get("name") or f.get("sel"), "required": True})
            continue
        try:
            if f.get("tag") == "select":
                try:
                    br.call(page.select_option, f["sel"], label=val)
                except Exception:
                    br.call(page.select_option, f["sel"], value=val)
            else:
                br.call(page.fill, f["sel"], val)
            filled.append({"field": f.get("label") or f.get("name"), "key": key, "value": val[:80]})
        except Exception:
            try:
                br.call(page.evaluate, SETTER_JS, {"sel": f["sel"], "value": val})
                filled.append({"field": f.get("label") or f.get("name"), "key": key, "value": val[:80]})
            except Exception as e:
                failed.append({"field": f.get("label") or f.get("name"), "reason": str(e)[:120]})

    submitted = False
    if do_submit:
        try:
            sel = br.call(page.evaluate, SUBMIT_JS)
            if sel:
                br.call(page.click, sel)
                br.call(page.wait_for_timeout, 3500)
                submitted = True
            else:
                failed.append({"field": "submit", "reason": "No submit/apply button found; finish it manually."})
        except Exception as e:
            failed.append({"field": "submit", "reason": str(e)[:160]})

    shot = ""
    try:
        _data, shot = br.call(scr.screenshot, False, 70)
    except Exception:
        pass

    lines = [f"Filled {len(filled)} field(s) on {C.domain(url) or url}"
             + (" and uploaded " + str(len(files_uploaded)) + " file(s)" if files_uploaded else "") + ".",
             ("SUBMITTED the application." if submitted else
              ("Dry run: the form is filled but NOT submitted. Review it, then call again with dry_run=false and submit=true."
               if dry_run else "Stopped before submit. Set submit=true to send it."))]
    if filled:
        lines.append("Filled: " + ", ".join(x["field"] or x["key"] for x in filled[:20]))
    if files_uploaded:
        lines.append("Uploaded: " + ", ".join(x["file"] for x in files_uploaded))
    if skipped:
        lines.append("Still empty (required): " + ", ".join(str(x["field"]) for x in skipped[:12]))
    if failed:
        lines.append("Could not fill: " + "; ".join(f"{x['field']}: {x['reason']}" for x in failed[:8]))
    lines.append("The browser tab is left open so you can check the page and submit it yourself.")
    payload = {"url": url, "filled": filled, "uploaded": files_uploaded, "skipped_required": skipped,
               "failed": failed, "submitted": submitted, "dry_run": dry_run, "screenshot": shot}
    return ToolResult("\n".join(lines), data=json.dumps(payload, ensure_ascii=False),
                      images=[shot] if shot else [], url=url,
                      untrusted="the online application form")
