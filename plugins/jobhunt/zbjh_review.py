"""Job Hunt: spellcheck, human-tone rewriting and pre-send document review."""
from __future__ import annotations

import json
import re
from pathlib import Path

import zbjh_common as C
from zbjh_common import ConnectorError, ToolResult

_SPELL = None
_SPELL_FAILED = False

REGION_WORDS = """zimbabwe zimbabwean harare bulawayo mutare gweru masvingo chinhoyi kadoma kwekwe
south africa johannesburg johannesburg cape town durban pretoria sandton midrand centurion gauteng
kwazulu natal limpopo mpumalanga free state north west eastern cape western cape africa african
nigeria lagos abuja kenya nairobi ghana accra uganda kampala tanzania dar es salaam zambia lusaka
botswana gaborone namibia windhoek mozambique maputo malawi lilongwe
united kingdom england scotland wales london manchester birmingham leeds glasgow edinburgh
europe european germany berlin munich france paris netherlands amsterdam spain madrid italy rome
portugal lisbon ireland dublin belgium brussels switzerland zurich austria vienna
united states america american canada canadian toronto vancouver australia sydney melbourne brisbane
new zealand auckland uae dubai abu dhabi saudi arabia qatar doha
english shona ndebele afrikaans zulu xhosa sesotho setswana portuguese french german spanish
zra sars paye vat cv referees referee matric o-level a-level o level a level
""".split()

EXTRA_WORDS = """ledger reconciliations creditors debtors bookkeeping payroll sage pastel quickbooks xero
stakeholders stakeholder liaising liaison onsite on-site offsite workflow workflows kpis kpi
procurement tendering quotations invoicing invoiced dispatch logistics warehousing stocktake
autocad revit solidworks plc haccp iso ohs sheq qa qc sql python javascript typescript nodejs
mysql postgresql mongodb aws azure devops docker kubernetes jenkins terraform linux ubuntu
github gitlab bitbucket api apis html css php laravel wordpress woocommerce shopify figma canva
photoshop illustrator indesign premiere excel powerpoint outlook sharepoint onedrive teams
linkedin facebook instagram whatsapp tiktok youtube seo sem google analytics ads adwords
crm salesforce hubspot zoho erp sap oracle navision dynamics
degree diploma certificate certificates bachelors bachelors honours honors masters mba phd
acca cima cfa cpa acis cips cipd nursing pharmacist lecturer teacher engineer technician
artisan electrician mechanic welder boilermaker fitter turner millwright
curriculum vitae resume cover letter application applications applicant shortlisted
availability negotiable remuneration salary benefits allowance allowances gratuity pension
internship internship graduate learnership bursary apprenticeship
""".split()

MONTHS = "january february march april may june july august september october november december".split()
COMMON_NAMES = set(REGION_WORDS) | set(EXTRA_WORDS) | set(MONTHS)

AI_PATTERNS = [
    (r"\butili[sz]e(d|s|ing)?\b", "use"), (r"\butili[sz]ation\b", "use"),
    (r"\bleverag(e|ed|es|ing)\b", "use"), (r"\bspearhead(ed|ing|s)?\b", "led"),
    (r"\bpassionate about\b", "interested in"), (r"\bpassionate\b", "keen"),
    (r"\bresults[- ]driven\b", "practical"), (r"\bdetail[- ]oriented\b", "careful with detail"),
    (r"\bteam player\b", "works well with others"), (r"\bgo[- ]getter\b", "self-starter"),
    (r"\bproven track record\b", "a record"), (r"\bthink outside the box\b", "find practical solutions"),
    (r"\bdynamic\b", "energetic"), (r"\brobust\b", "solid"), (r"\bseamless(ly)?\b", "smooth"),
    (r"\bcutting[- ]edge\b", "modern"), (r"\bstate[- ]of[- ]the[- ]art\b", "modern"),
    (r"\binnovative\b", "new"), (r"\bholistic\b", "complete"), (r"\bparadigm shift\b", "change"),
    (r"\bparadigm\b", "model"), (r"\becosystem\b", "environment"), (r"\bempower(ed|ing|s)?\b", "support"),
    (r"\bstreamlin(e|ed|ing)\b", "simplify"), (r"\boptimi[sz](e|ed|ing|ation)\b", "improve"),
    (r"\bbest[- ]in[- ]class\b", "strong"), (r"\bworld[- ]class\b", "excellent"),
    (r"\bvalue[- ]add(ed|ing)?\b", "useful"), (r"\bimpactful\b", "effective"),
    (r"\bdelve\b", "look"), (r"\btapestry\b", "mix"), (r"\bmultifaceted\b", "varied"),
    (r"\bunderscor(e|ed|ing)\b", "show"), (r"\bpivotal\b", "key"), (r"\bcrucial\b", "important"),
    (r"\bcomprehensive\b", "full"), (r"\bmeticulous(ly)?\b", "careful"), (r"\badept\b", "skilled"),
    (r"\bshowcas(e|ed|ing)\b", "show"), (r"\bfurthermore\b", "also"), (r"\bmoreover\b", "also"),
    (r"\bin today's fast[- ]paced\b", "in today's"), (r"\bi am excited to apply\b", "I'd like to apply"),
    (r"\bi am writing to express my (keen )?interest in\b", "I'd like to apply for"),
    (r"\bi am writing to apply for\b", "I'd like to apply for"),
    (r"\bi am writing to apply\b", "I'd like to apply for"), (r"\bthrilled\b", "glad"),
    (r"\bsynerg(y|ies|istic)\b", "teamwork"), (r"\bgame[- ]changer\b", "improvement"),
    (r"\bdeep dive\b", "close look"), (r"\bnavigate\b", "handle"),
    (r"\bdelv(e|ed|ing) into\b", "looked at"), (r"\bever[- ]evolving\b", "changing"),
]

WEAK_PHRASES = ["responsible for", "duties included", "helped with", "worked on", "assisted with",
                "involved in", "tasked with", "participated in"]

ACTION_VERBS = set("""achieved administered analysed analyzed built coached coordinated created delivered
designed developed directed drove established generated implemented improved increased initiated
introduced launched led managed mentored negotiated organised organized oversaw planned produced
reduced resolved reviewed saved secured sold streamlined supervised trained won wrote prepared
maintained processed reconciled scheduled supported handled recruited monitored audited
""".split())


def _spell():
    global _SPELL, _SPELL_FAILED
    if _SPELL is not None or _SPELL_FAILED:
        return _SPELL
    try:
        from spellchecker import SpellChecker
        _SPELL = SpellChecker()
    except Exception:
        _SPELL_FAILED = True
    return _SPELL


def _allowlist(prof: dict, extra: list[str] | None = None) -> set[str]:
    words = set(COMMON_NAMES)
    for key in ("full_name", "headline", "location", "nationality", "summary", "writing_style"):
        words.update(re.findall(r"[A-Za-z][A-Za-z'\-]+", str(prof.get(key) or "")))
    for e in prof.get("experience") or []:
        if isinstance(e, dict):
            for k in ("company", "title", "location"):
                words.update(re.findall(r"[A-Za-z][A-Za-z'\-]+", str(e.get(k) or "")))
    for ed in prof.get("education") or []:
        if isinstance(ed, dict):
            for k in ("institution", "qualification", "location"):
                words.update(re.findall(r"[A-Za-z][A-Za-z'\-]+", str(ed.get(k) or "")))
    for r in prof.get("referees") or []:
        if isinstance(r, dict):
            for k in ("name", "title", "company", "email"):
                words.update(re.findall(r"[A-Za-z][A-Za-z'\-]+", str(r.get(k) or "")))
    for c in prof.get("certifications") or []:
        if isinstance(c, dict):
            for k in ("name", "issuer"):
                words.update(re.findall(r"[A-Za-z][A-Za-z'\-]+", str(c.get(k) or "")))
    for p in prof.get("projects") or []:
        if isinstance(p, dict):
            words.update(re.findall(r"[A-Za-z][A-Za-z'\-]+", str(p.get("name") or "")))
    for x in extra or []:
        words.update(re.findall(r"[A-Za-z][A-Za-z'\-]+", str(x or "")))
    return {w.lower() for w in words if len(w) > 2}


def _close_domain_word(word: str, allow: set[str]) -> str:
    """Prefer a real domain word (payroll, reconciliations...) over an odd dictionary hit."""
    best, best_d = "", 3
    for cand in allow:
        if abs(len(cand) - len(word)) > 1 or cand == word:
            continue
        # cheap edit distance, bail out above 2
        prev = list(range(len(cand) + 1))
        for i, ch in enumerate(word, 1):
            cur = [i]
            for j, c2 in enumerate(cand, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ch != c2)))
            prev = cur
            if min(prev) > 2:
                break
        d = prev[-1]
        if d < best_d:
            best, best_d = cand, d
    return best if best_d <= 2 else ""


def spellcheck_text(text: str, prof: dict, extra: list[str] | None = None) -> list[dict]:
    checker = _spell()
    if checker is None:
        return []
    allow = _allowlist(prof, extra)
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", text or "")
    # Proper nouns that repeat are almost certainly names/places: do not nag about them.
    seen: dict[str, int] = {}
    for t in tokens:
        if t[:1].isupper():
            seen[t.lower()] = seen.get(t.lower(), 0) + 1
    allow.update({w for w, n in seen.items() if n >= 2})
    candidates = []
    for t in tokens:
        low = t.lower().strip("'-")
        if len(low) < 3 or low in allow or low.isdigit() or any(ch.isdigit() for ch in low):
            continue
        if t.isupper():          # acronyms: SME, USD, ZRA, ACCA
            continue
        if low.endswith("'s"):
            low = low[:-2]
        if low in allow:
            continue
        if low.endswith(("s", "es", "ed", "ing", "ly")) and low[:-1] in allow:
            continue
        candidates.append((t, low))
    unknown = checker.unknown([low for _t, low in candidates])
    if not unknown:
        return []
    out, seen_words = [], set()
    for token, low in candidates:
        if low not in unknown or low in seen_words:
            continue
        seen_words.add(low)
        suggestion = checker.correction(low) or ""
        domain_hit = _close_domain_word(low, allow)
        if domain_hit and domain_hit != low:
            suggestion = domain_hit
        if suggestion and suggestion != low:
            out.append({"word": token, "suggestion": suggestion,
                        "alternatives": sorted(checker.candidates(low) or [])[:4]})
    return out


def _fix_spelling(text: str, issues: list[dict]) -> str:
    fixed = text
    for item in issues:
        fixed = re.sub(r"\b" + re.escape(item["word"]) + r"\b", item["suggestion"], fixed)
    return fixed


def spellcheck(ctx, a):
    prof = C.load_profile(ctx)
    text = str(a.get("text") or "")
    path = str(a.get("path") or "").strip()
    if not text and path:
        text = C.read_doc_text(ctx, path)
    if not text:
        raise ConnectorError("Give text or a workspace path to check.")
    issues = spellcheck_text(text, prof, [a.get("context") or ""])
    fixed = _fix_spelling(text, issues) if a.get("fix") else ""
    if not issues:
        return ToolResult(f"No spelling problems found in {len(text.split())} words.")
    lines = [f"{len(issues)} possible spelling issue(s):"]
    for i in issues[:40]:
        lines.append(f"  {i['word']} -> {i['suggestion']}  (also: {', '.join(i['alternatives']) or 'n/a'})")
    if a.get("fix"):
        out_path = C.resolve(ctx, path) if path else C.resolve(ctx, C.BASE + "/spellchecked.txt")
        out_path = out_path.with_name(out_path.stem + "-corrected.txt")
        C.ensure_parent(out_path).write_text(fixed, encoding="utf-8")
        lines.append(f"Corrected text saved to {C.rel(ctx, out_path)}.")
        return ToolResult("\n".join(lines), path=C.rel(ctx, out_path), data=fixed)
    return ToolResult("\n".join(lines), data=json.dumps(issues, ensure_ascii=False))


def humanize_text(ctx, a):
    text = str(a.get("text") or "").strip()
    if not text and a.get("path"):
        text = C.read_doc_text(ctx, a["path"])
    if not text:
        raise ConnectorError("Give text or a workspace path to rewrite.")
    original = text
    changes = []

    def sub(pattern, repl):
        nonlocal text
        new = re.sub(pattern, repl, text, flags=re.I)
        if new != text:
            changes.append(pattern)
            text = new

    for pattern, repl in AI_PATTERNS:
        sub(pattern, repl)
    if str(a.get("contractions", True)).lower() not in ("false", "0", "no"):
        for pattern, repl in ((r"\bI am\b", "I'm"), (r"\bI have\b", "I've"), (r"\bI will\b", "I'll"),
                              (r"\bI would\b", "I'd"), (r"\bdo not\b", "don't"), (r"\bcannot\b", "can't"),
                              (r"\bwill not\b", "won't"), (r"\bit is\b", "it's"), (r"\bthat is\b", "that's")):
            sub(pattern, repl)
    text = text.replace(" — ", ", ").replace("—", ", ").replace("–", "-")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if str(a.get("rewrite", False)).lower() in ("true", "1", "yes"):
        style = a.get("style") or "plain, warm and specific"
        prompt = ("Rewrite the text so it reads like a real person wrote it. Keep every fact, name, date and number. "
                  "Use short sentences, active verbs and normal contractions. Remove AI clichés. Do not add new claims. "
                  f"Style: {style}. Return only the rewritten text.\n\n{text[:9000]}")
        better = C.llm(ctx, "You are a plain-English editor for CVs and job applications.", prompt, 1400)
        if better:
            text = better
            changes.append("AI rewrite pass")

    out = ToolResult(f"Rewrote {len(original.split())} words; {len(changes)} style rule(s) applied."
                     + (" An AI rewrite pass was also applied." if "AI rewrite pass" in changes else ""),
                     data=text)
    if a.get("path"):
        p = C.resolve(ctx, a["path"])
        out_path = p.with_name(p.stem + "-human.txt")
        C.ensure_parent(out_path).write_text(text, encoding="utf-8")
        out.path = C.rel(ctx, out_path)
        out.text += f" Saved to {out.path}."
    return out


def _readable_score(text: str) -> int:
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    if not sentences or not words:
        return 0
    avg = len(words) / len(sentences)
    return max(0, min(100, int(110 - avg * 2.2)))


def _docx_facts(ctx, path: Path) -> dict:
    if path.suffix.lower() != ".docx":
        return {}
    try:
        from docx import Document
        doc = Document(str(path))
    except Exception:
        return {}
    fonts = set()
    for para in doc.paragraphs:
        for run in para.runs:
            if run.font.name:
                fonts.add(run.font.name)
    return {"tables": len(doc.tables), "images": len(doc.inline_shapes), "fonts": sorted(fonts)}


def review_document(ctx, a):
    path = (a.get("path") or a.get("file") or "").strip()
    if not path:
        raise ConnectorError("Give the document path (PDF, DOCX or TXT).")
    p = C.resolve(ctx, path)
    text = C.read_doc_text(ctx, path)
    if not text.strip():
        raise ConnectorError("That document has no extractable text. If it is a scanned PDF, OCR it first.")
    prof = C.load_profile(ctx)
    from zbjh_cv import region_profile
    region = region_profile(a.get("region") or prof.get("default_region"))
    job = C.get_job(ctx, a.get("job_id") or "") if a.get("job_id") else None
    desc = a.get("job_description") or (job or {}).get("description") or ""
    if not desc and a.get("job_url"):
        from zbjh_search import read_job
        read_job(ctx, {"url": a["job_url"]})
        job = C.get_job(ctx, a["job_url"])
        desc = (job or {}).get("description", "")

    issues: list[dict] = []

    def add(severity, area, message, fix=""):
        issues.append({"severity": severity, "area": area, "message": message, "fix": fix})

    low = text.lower()
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", text)
    word_count = len(words)
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    phones = re.findall(r"(\+?\d[\d\s().-]{7,}\d)", text)

    if not emails:
        add("high", "contact", "No email address found.", "Add a professional email under your name.")
    if not phones:
        add("high", "contact", "No phone number found.", "Add a phone number with the country code.")
    if not any(h in text.upper() for h in ("SUMMARY", "PROFILE", "OBJECTIVE")):
        add("medium", "structure", "No summary/profile section.", "Add a 2-3 sentence professional summary.")
    if "EXPERIENCE" not in text.upper() and "EMPLOYMENT" not in text.upper():
        add("high", "structure", "No experience section.", "Add a PROFESSIONAL EXPERIENCE section.")
    if "EDUCATION" not in text.upper():
        add("medium", "structure", "No education section.", "Add an EDUCATION section with your highest qualification.")
    if "SKILL" not in text.upper():
        add("medium", "structure", "No skills section.", "Add a CORE SKILLS section using the advert's wording.")

    if word_count < 220:
        add("medium", "length", f"Only {word_count} words: the document looks thin.", "Add measurable achievements to each role.")
    if word_count > 1100:
        add("low", "length", f"{word_count} words: long for a CV.", "Trim older roles to 1-2 bullets.")

    if not re.search(r"\b(19|20)\d{2}\b", text):
        add("medium", "dates", "No years found.", "Add start/end years for each role and qualification.")

    bullets = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip().startswith(("•", "-", "*"))]
    weak = [b for b in bullets if b.lower().startswith(tuple(WEAK_PHRASES))]
    if weak:
        add("medium", "impact", f"{len(weak)} bullet(s) start weakly (e.g. 'responsible for').",
            "Start with an action verb: Led, Built, Cut, Improved, Managed.")
    starts = [b.split()[0].lower() for b in bullets if b.split()]
    if bullets and sum(1 for s in starts if s in ACTION_VERBS) / len(starts) < 0.5:
        add("low", "impact", "Fewer than half of the bullets start with an action verb.", "Rewrite bullets to start with a verb.")

    if not re.search(r"\d+\s?%|[$R]\s?\d|\b\d{2,}\b", text):
        add("high", "impact", "No numbers or measurable results.", "Quantify: team size, budget, %, volume, time saved.")

    first_person = len(re.findall(r"\bI\b", text))
    if first_person > 2:
        add("medium", "tone", f"The CV uses 'I' {first_person} times.", "CVs normally drop 'I': 'Managed a team of 5'.")

    ai_hits = []
    for pattern, _repl in AI_PATTERNS:
        m = re.search(pattern, text, re.I)
        if m:
            ai_hits.append(m.group(0))
    if ai_hits:
        add("medium", "tone", "AI-sounding phrases: " + ", ".join(sorted(set(ai_hits))[:8]),
            "Run jobhunt_humanize_text on the summary and bullets.")

    if region.get("referees") == "full" and "REFERENCE" not in text.upper():
        add("low", "region", f"{region['label']} employers usually expect referees in the CV.",
            "Add two contactable referees, or add them to the profile and regenerate.")
    if region.get("referees") == "on_request" and "REFERENCES AVAILABLE" in text.upper():
        add("low", "region", "UK/US/Canadian CVs usually omit 'references available on request'.",
            "Remove that line to save space.")

    facts = _docx_facts(ctx, p)
    if facts.get("tables"):
        add("medium", "ats", f"The Word file contains {facts['tables']} table(s).",
            "Some ATS readers scramble tables. Use plain paragraphs for the CV.")
    if facts.get("images"):
        add("low", "ats", f"The Word file contains {facts['images']} image(s).",
            "Photos and logos can confuse ATS parsing; keep the ATS version text-only.")
    if facts.get("fonts"):
        odd = [f for f in facts["fonts"] if f.lower() not in ("calibri", "carlito", "arial", "helvetica", "times new roman", "georgia", "garamond")]
        if odd:
            add("low", "font", "Unusual font(s): " + ", ".join(odd[:4]), "Use Calibri (or Arial) for a clean, ATS-safe CV.")

    coverage = None
    if desc:
        from zbjh_search import extract_keywords
        kw = extract_keywords(desc)
        wanted = [s for s in kw.get("skills", []) if s]
        have = [s for s in wanted if s.lower() in low or any(a.split()[0] in low for a in [s.lower()])]
        coverage = int(100 * len(have) / max(1, len(wanted))) if wanted else None
        if coverage is not None and coverage < 60:
            missing = [s for s in wanted if s not in have][:8]
            add("high", "keywords", f"Only {coverage}% of the advert's skills appear in the document.",
                "Add these where true: " + ", ".join(missing))

    spelling = spellcheck_text(text, prof, [a.get("context") or "", (job or {}).get("company", "")])
    for item in spelling[:12]:
        add("medium", "spelling", f"'{item['word']}' may be a typo.", f"Replace with '{item['suggestion']}'.")

    penalty = {"high": 14, "medium": 6, "low": 2}
    score = 100 - sum(penalty[i["severity"]] for i in issues)
    if coverage is not None:
        score = min(score, 55 + int(coverage * 0.45))
    score = max(0, min(100, score))

    if str(a.get("apply_fixes", False)).lower() in ("true", "1", "yes") and spelling:
        side = p.with_suffix(".json")
        if side.is_file():
            from zbjh_cv import update_cv
            update_cv(ctx, {"file": C.rel(ctx, side),
                            "changes": [{"find": i["word"], "replace": i["suggestion"]} for i in spelling]})
            add("low", "fixed", "Spelling fixes were applied and the CV was re-rendered.", "")
        elif p.suffix.lower() == ".docx":
            try:
                from docx import Document
                doc = Document(str(p))
                for para in doc.paragraphs:
                    for run in para.runs:
                        for item in spelling:
                            run.text = re.sub(r"\b" + re.escape(item["word"]) + r"\b", item["suggestion"], run.text)
                doc.save(str(p))
                add("low", "fixed", "Spelling fixes were applied to the Word file.", "")
            except Exception as e:
                add("low", "fixed", f"Could not apply fixes: {e}", "Fix the words manually.")

    high = [i for i in issues if i["severity"] == "high"]
    med = [i for i in issues if i["severity"] == "medium"]
    lowi = [i for i in issues if i["severity"] == "low"]
    lines = [f"Review of {p.name}: {score}/100"
             + (f" · advert keyword coverage {coverage}%" if coverage is not None else "")
             + f" · {word_count} words · readability {_readable_score(text)}/100"]
    for label, group in (("MUST FIX", high), ("SHOULD FIX", med), ("NICE TO FIX", lowi)):
        if group:
            lines.append(label + ":")
            for i in group[:12]:
                lines.append(f"  - [{i['area']}] {i['message']}" + (f"  -> {i['fix']}" if i["fix"] else ""))
    if not issues:
        lines.append("No problems found. It is ready to send.")
    payload = {"path": C.rel(ctx, p), "score": score, "words": word_count, "keyword_coverage": coverage,
               "issues": issues, "spelling": spelling[:40], "text": text[:20000]}
    return ToolResult("\n".join(lines), data=json.dumps(payload, ensure_ascii=False),
                      untrusted="the reviewed document")


def extract_keywords_tool(ctx, a):
    text = str(a.get("text") or "")
    if not text and a.get("job_id"):
        job = C.get_job(ctx, a["job_id"])
        text = (job or {}).get("description", "")
    if not text and a.get("url"):
        from zbjh_search import read_job
        read_job(ctx, {"url": a["url"]})
        text = (C.get_job(ctx, a["url"]) or {}).get("description", "")
    if not text:
        raise ConnectorError("Give text, job_id or url.")
    from zbjh_search import extract_keywords
    kw = extract_keywords(text)
    lines = ["Skills: " + (", ".join(kw["skills"]) or "none detected"),
             "Soft skills: " + (", ".join(kw["soft_skills"]) or "none detected"),
             "Seniority: " + kw["seniority"] + (" | years: " + kw["years_experience"] if kw["years_experience"] else ""),
             "Qualifications: " + (", ".join(kw["qualifications"]) or "not stated"),
             "Application needs: " + (", ".join(k for k, v in kw["application_requirements"].items() if v) or "CV only"),
             "Red flags: " + (", ".join(kw["red_flags"]) or "none"),
             "Top terms: " + ", ".join(kw["top_terms"][:20])]
    return ToolResult("\n".join(lines), data=json.dumps(kw, ensure_ascii=False))
