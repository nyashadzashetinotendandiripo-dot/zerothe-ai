"""Job Hunt: job boards, search, advert reading, keyword analysis and ranking."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import zbjh_common as C
from zbjh_common import ConnectorError, ToolResult

# ---------------------------------------------------------------- skill vocabulary
SKILL_DB = {
    "Accounting": ["accounting", "bookkeeping", "book keeping", "ledger", "reconciliations"],
    "Accounts Payable": ["accounts payable", "creditors", "ap clerk"],
    "Accounts Receivable": ["accounts receivable", "debtors", "collections"],
    "Payroll": ["payroll", "paye", "payroll administration", "sage payroll"],
    "Tax": ["tax", "vat", "zimbabwe revenue authority", "zra", "sars", "taxation"],
    "Auditing": ["audit", "auditing", "internal audit", "external audit"],
    "Financial Reporting": ["financial reporting", "financial statements", "ifrs", "gaap"],
    "Budgeting": ["budgeting", "forecasting", "budget", "cash flow"],
    "Excel": ["excel", "spreadsheets", "pivot tables", "vlookup", "xlookup", "advanced excel"],
    "Sage": ["sage", "sage pastel", "pastel", "sage evolution", "sage 300"],
    "QuickBooks": ["quickbooks", "quick books"],
    "Xero": ["xero"],
    "SAP": ["sap", "sap fico", "sap mm"],
    "Oracle": ["oracle", "oracle erp", "oracle financials"],
    "Microsoft Dynamics": ["dynamics", "microsoft dynamics", "navision", "business central"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Data Analysis": ["data analysis", "data analytics", "analytics", "dashboards", "reporting analyst"],
    "SQL": ["sql", "mysql", "postgresql", "postgres", "sql server", "t-sql", "pl/sql"],
    "Python": ["python", "pandas", "numpy", "django", "flask", "fastapi"],
    "JavaScript": ["javascript", "js", "typescript", "node.js", "nodejs", "react", "vue", "angular"],
    "Java": ["java", "spring boot", "spring"],
    "C#": ["c#", "csharp", ".net", "dotnet", "asp.net"],
    "PHP": ["php", "laravel"],
    "Ruby": ["ruby", "rails"],
    "Go": ["golang", " go "],
    "HTML/CSS": ["html", "css", "sass", "tailwind"],
    "WordPress": ["wordpress", "elementor", "woocommerce"],
    "Shopify": ["shopify", "liquid"],
    "API Integration": ["api", "rest api", "restful", "graphql", "integrations"],
    "Cloud": ["aws", "azure", "google cloud", "gcp", "cloud"],
    "DevOps": ["devops", "docker", "kubernetes", "ci/cd", "terraform", "jenkins"],
    "Linux": ["linux", "ubuntu", "unix", "bash", "shell scripting"],
    "Networking": ["networking", "tcp/ip", "cisco", "ccna", "firewall", "vpn", "lan", "wan"],
    "Cybersecurity": ["cybersecurity", "cyber security", "information security", "iso 27001", "penetration testing"],
    "IT Support": ["it support", "helpdesk", "help desk", "desktop support", "technical support", "troubleshooting"],
    "Windows Server": ["windows server", "active directory", "group policy", "microsoft 365", "office 365"],
    "Database Administration": ["database administration", "dba", "database administrator"],
    "Project Management": ["project management", "project manager", "pmp", "prince2", "agile", "scrum", "kanban"],
    "Business Analysis": ["business analysis", "business analyst", "requirements gathering", "process mapping"],
    "Product Management": ["product management", "product manager", "product owner", "roadmap"],
    "Sales": ["sales", "business development", "account executive", "targets", "lead generation"],
    "Marketing": ["marketing", "digital marketing", "seo", "sem", "google ads", "social media", "content marketing"],
    "Brand Management": ["brand management", "brand manager", "brand strategy"],
    "Copywriting": ["copywriting", "copywriter", "content writing", "editorial"],
    "Customer Service": ["customer service", "customer support", "call centre", "call center", "client service"],
    "Administration": ["administration", "administrative", "office administration", "secretarial", "receptionist"],
    "Human Resources": ["human resources", "hr", "recruitment", "talent acquisition", "industrial relations", "hris"],
    "Training and Development": ["training", "learning and development", "facilitation", "coaching"],
    "Procurement": ["procurement", "purchasing", "supply chain", "sourcing", "tendering"],
    "Logistics": ["logistics", "warehouse", "inventory", "distribution", "freight", "clearing and forwarding"],
    "Fleet Management": ["fleet management", "transport management", "vehicle tracking"],
    "Operations Management": ["operations management", "operations manager", "production management"],
    "Quality Assurance": ["quality assurance", "quality control", "qa", "qc", "iso 9001", "haccp"],
    "Health and Safety": ["health and safety", "she", "ohs", "osha", "safety officer", "risk assessment"],
    "Civil Engineering": ["civil engineering", "structural", "construction", "autocad", "revit", "quantity surveying"],
    "Mechanical Engineering": ["mechanical engineering", "maintenance engineering", "solidworks", "catia"],
    "Electrical Engineering": ["electrical engineering", "electrician", "plc", "instrumentation"],
    "Mining": ["mining", "geology", "mineral processing", "underground", "open pit"],
    "Agriculture": ["agriculture", "agronomy", "horticulture", "livestock", "irrigation"],
    "Nursing": ["nursing", "registered nurse", "patient care", "clinical"],
    "Pharmacy": ["pharmacy", "pharmacist", "dispensing"],
    "Teaching": ["teaching", "teacher", "lecturer", "curriculum", "classroom management"],
    "Legal": ["legal", "litigation", "contracts", "company secretarial", "compliance", "llb"],
    "Public Relations": ["public relations", "communications", "media relations", "stakeholder engagement"],
    "Graphic Design": ["graphic design", "photoshop", "illustrator", "indesign", "canva", "figma"],
    "Video Editing": ["video editing", "premiere pro", "after effects", "videography"],
    "Photography": ["photography", "photographer"],
    "UI/UX": ["ui/ux", "ux design", "user experience", "wireframes", "prototyping"],
    "Research": ["research", "monitoring and evaluation", "m&e", "data collection", "survey"],
    "Monitoring and Evaluation": ["monitoring and evaluation", "m&e", "mel", "logframe"],
    "Grant Management": ["grant management", "donor reporting", "proposal writing", "fundraising"],
    "Translation": ["translation", "translator", "interpretation", "bilingual"],
    "Driving": ["driver", "driving", "class 4 licence", "class 2 licence", "code 10", "code 14"],
    "Reception": ["reception", "front desk", "switchboard"],
    "Event Management": ["event management", "events coordinator", "conference"],
    "Real Estate": ["real estate", "property management", "valuation", "letting"],
    "Insurance": ["insurance", "underwriting", "claims", "broker"],
    "Banking": ["banking", "credit analysis", "loan", "treasury", "tellering"],
    "Microfinance": ["microfinance", "micro finance", "credit officer"],
    "Telecommunications": ["telecommunications", "telecoms", "fibre", "fiber", "rf", "gsm"],
    "Renewable Energy": ["solar", "renewable energy", "pv", "inverter", "energy efficiency"],
    "Salesforce": ["salesforce", "crm", "hubspot", "zoho"],
    "Artificial Intelligence": ["artificial intelligence", "machine learning", "ai", "llm", "nlp", "data science"],
}

SOFT_SKILLS = ["communication", "teamwork", "leadership", "problem solving", "problem-solving", "time management",
               "attention to detail", "interpersonal", "organisational", "organizational", "analytical",
               "negotiation", "adaptability", "critical thinking", "customer focus", "decision making",
               "stakeholder management", "multitasking", "self-motivated", "work under pressure"]

STOPWORDS = set("""a about above after again against all am an and any are aren't as at be because been before being
below between both but by can cannot could couldn't did didn't do does doesn't doing don't down during each few for from
further had hadn't has hasn't have haven't having he her here hers herself him himself his how i if in into is isn't it
its itself let's me more most mustn't my myself no nor not of off on once only or other ought our ours ourselves out over
own same shan't she should shouldn't so some such than that the their theirs them themselves then there these they this
those through to too under until up very was wasn't we were weren't what when where which while who whom why with won't
would wouldn't you your yours yourself yourselves will shall may might must also within across per via etc eg ie role
job position applicant candidates candidate company team work working works experience experienced years year required
require requirements responsibilities responsibility duties duties key main new ability able strong good excellent
knowledge understanding skills skill including include includes provide support ensure ensuring help join looking
applications apply application please salary benefits opportunity opportunities deadline closing""".split())

APPLICATION_HINTS = {
    "cover letter": ["cover letter", "covering letter", "motivation letter"],
    "cv": ["cv", "resume", "curriculum vitae"],
    "certificates": ["certified copies", "certificates", "certificate", "qualifications attached", "academic certificates"],
    "transcripts": ["transcript", "academic record"],
    "references": ["referees", "references", "contactable references"],
    "portfolio": ["portfolio", "work samples", "showreel"],
    "police clearance": ["police clearance", "police report", "criminal record check"],
    "driver licence": ["driver's licence", "drivers licence", "driver license", "valid licence"],
    "id copy": ["copy of id", "id copy", "identity document", "passport copy"],
}


def _salary(*parts) -> str:
    vals = [str(p) for p in parts if p not in (None, "", 0, "0")]
    return " - ".join(vals)


def _posted_str(value) -> str:
    """Boards return dates as strings, unix seconds or milliseconds. Normalise to YYYY-MM-DD."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            ts = float(value)
            if ts > 1e11:      # milliseconds
                ts /= 1000.0
            if ts > 1e8:       # plausible epoch
                return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return ""
        return ""
    text = C.collapse(value)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    m = re.match(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return text[:40]


def _norm(title, company, location, url, source, **kw) -> dict:
    title = C.collapse(title)
    company = C.collapse(company)
    location = C.collapse(location)
    job = {
        "title": title or "Untitled role",
        "company": company,
        "location": location or kw.pop("fallback_location", ""),
        "url": url or "",
        "source": source,
        "posted": _posted_str(kw.pop("posted", "")),
        "salary": kw.pop("salary", ""),
        "type": kw.pop("type", ""),
        "remote": bool(kw.pop("remote", False)) or bool(re.search(r"\bremote\b", location, re.I)),
        "snippet": C.collapse(kw.pop("snippet", "")),
        "tags": kw.pop("tags", []) or [],
        "description": kw.pop("description", ""),
        "region": kw.pop("region", ""),
    }
    job["id"] = C.job_id(job["url"], job["title"], job["company"])
    return job


# ---------------------------------------------------------------- API adapters
REMOTIVE_CATEGORIES = [
    (r"account|finance|audit|tax|bookkeep|payroll|treasur|credit|bank", "finance-legal"),
    (r"\bhr\b|human resources|recruit|talent|people", "hr"),
    (r"market|seo|content|social media|brand|growth", "marketing"),
    (r"sales|business development|account executive|partnership", "sales-business"),
    (r"design|ux|ui|graphic|illustrat", "design"),
    (r"data|analytic|machine learning|scientist", "data"),
    (r"devops|sre|infrastructure|cloud|platform", "devops-sysadmin"),
    (r"qa|test|quality", "qa"),
    (r"product manager|product owner|product lead", "product"),
    (r"project manager|programme|program manager|scrum", "project-management"),
    (r"support|customer service|helpdesk", "customer-support"),
    (r"writ|editor|copywrit|translator", "writing"),
    (r"engineer|developer|software|programmer|full.?stack|backend|frontend", "software-dev"),
]


def _remotive(ctx, q, loc, limit, companies=None):
    params = {"limit": min(max(limit * 3, 20), 100)}
    if q:
        params["search"] = q
        for pattern, category in REMOTIVE_CATEGORIES:
            if re.search(pattern, q, re.I):
                params["category"] = category
                break
    data = C.fetch_json(ctx, "https://remotive.com/api/remote-jobs", params=params)
    out = []
    for j in data.get("jobs", []) or []:
        out.append(_norm(j.get("title"), j.get("company_name"), j.get("candidate_required_location"),
                         j.get("url"), "Remotive", posted=j.get("publication_date"),
                         salary=C.collapse(j.get("salary")), type=j.get("job_type"), remote=True,
                         description=C.strip_html(j.get("description", "")), tags=j.get("tags") or []))
    return _client_filter(out, q)


def _arbeitnow(ctx, q, loc, limit, companies=None):
    out = []
    for page in (1, 2):
        data = C.fetch_json(ctx, "https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        rows = data.get("data", []) if isinstance(data, dict) else []
        if not rows:
            break
        for j in rows:
            out.append(_norm(j.get("title"), j.get("company_name"), j.get("location"), j.get("url"),
                             "Arbeitnow", posted=j.get("created_at"), remote=j.get("remote"),
                             type=", ".join(j.get("job_types") or []), tags=j.get("tags") or [],
                             description=C.strip_html(j.get("description", "")), region="europe"))
        if not data.get("links", {}).get("next"):
            break
    return _client_filter(out, q)


def _jobicy(ctx, q, loc, limit, companies=None):
    params = {"count": min(max(limit * 3, 20), 50)}
    if q:
        params["tag"] = q
    data = C.fetch_json(ctx, "https://jobicy.com/api/v2/remote-jobs", params=params)
    out = []
    for j in data.get("jobs", []) or []:
        salary = _salary(j.get("annualSalaryMin"), j.get("annualSalaryMax"))
        out.append(_norm(j.get("jobTitle"), j.get("companyName"), j.get("jobGeo"), j.get("url"),
                         "Jobicy", posted=j.get("pubDate"), salary=salary, type=j.get("jobType"),
                         remote=True, snippet=j.get("jobExcerpt"), description=C.strip_html(j.get("jobDescription", "")),
                         tags=[j.get("jobIndustry") or ""] + (j.get("jobLevel") and [j["jobLevel"]] or [])))
    return out


def _himalayas(ctx, q, loc, limit, companies=None):
    out = []
    for offset in (0, 100):
        data = C.fetch_json(ctx, "https://himalayas.app/jobs/api", params={"limit": 100, "offset": offset})
        rows = data.get("jobs", []) if isinstance(data, dict) else []
        if not rows:
            break
        for j in rows:
            locs = j.get("locationRestrictions") or []
            loc_text = ", ".join(locs) if isinstance(locs, list) else str(locs)
            out.append(_norm(j.get("title"), j.get("companyName"), loc_text, j.get("applicationLink") or j.get("guid"),
                             "Himalayas", posted=j.get("pubDate"), salary=_salary(j.get("minSalary"), j.get("maxSalary")),
                             remote=True, description=C.strip_html(j.get("description", "")),
                             tags=j.get("categories") or [], type=j.get("employmentType")))
        if len(rows) < 100:
            break
    return _client_filter(out, q)


def _remoteok(ctx, q, loc, limit, companies=None):
    data = C.fetch_json(ctx, "https://remoteok.com/api")
    out = []
    for j in (data if isinstance(data, list) else []):
        if not isinstance(j, dict) or not j.get("position"):
            continue
        out.append(_norm(j.get("position"), j.get("company"), j.get("location"), j.get("url") or
                         f"https://remoteok.com/remote-jobs/{j.get('id', '')}", "RemoteOK",
                         posted=j.get("date"), salary=_salary(j.get("salary_min"), j.get("salary_max")),
                         remote=True, tags=j.get("tags") or [], description=C.strip_html(j.get("description", ""))))
    return _client_filter(out, q)


def _weworkremotely(ctx, q, loc, limit, companies=None):
    out = []
    for feed in ("https://weworkremotely.com/categories/remote-programming-jobs.rss",
                 "https://weworkremotely.com/categories/remote-design-jobs.rss",
                 "https://weworkremotely.com/categories/remote-marketing-jobs.rss"):
        try:
            xml = C.fetch_text(ctx, feed)
        except Exception:
            continue
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(xml, "xml")
        except Exception:
            continue
        for item in soup.find_all("item"):
            title = item.find("title").get_text() if item.find("title") else ""
            link = item.find("link").get_text() if item.find("link") else ""
            region = item.find("region").get_text() if item.find("region") else "Remote"
            company = title.split(":", 1)[0] if ":" in title else ""
            role = title.split(":", 1)[1] if ":" in title else title
            out.append(_norm(role, company, region, link, "We Work Remotely",
                             posted=item.find("pubDate").get_text() if item.find("pubDate") else "",
                             remote=True, description=C.strip_html(item.find("description").get_text() if item.find("description") else "")))
    return _client_filter(out, q)


def _lever(ctx, q, loc, limit, companies=None):
    out = []
    for site in companies or []:
        site = site.split(":")[-1].strip()
        if not site:
            continue
        try:
            data = C.fetch_json(ctx, f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"})
        except Exception:
            continue
        for j in (data if isinstance(data, list) else []):
            cat = j.get("categories") or {}
            out.append(_norm(j.get("text"), site, cat.get("location"), j.get("hostedUrl"), f"Lever/{site}",
                             posted=datetime.utcfromtimestamp((j.get("createdAt") or 0) / 1000).strftime("%Y-%m-%d") if j.get("createdAt") else "",
                             type=cat.get("commitment"), description=C.strip_html(j.get("descriptionPlain") or j.get("description") or "")))
    return _client_filter(out, q)


def _greenhouse(ctx, q, loc, limit, companies=None):
    out = []
    for token in companies or []:
        token = token.split(":")[-1].strip()
        if not token:
            continue
        try:
            data = C.fetch_json(ctx, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                                params={"content": "true"})
        except Exception:
            continue
        for j in (data.get("jobs", []) if isinstance(data, dict) else []):
            loc_name = (j.get("location") or {}).get("name", "")
            out.append(_norm(j.get("title"), token, loc_name, j.get("absolute_url"), f"Greenhouse/{token}",
                             posted=(j.get("updated_at") or "")[:10],
                             description=C.strip_html(j.get("content", ""))))
    return _client_filter(out, q)


def _workable(ctx, q, loc, limit, companies=None):
    out = []
    for sub in companies or []:
        sub = sub.split(":")[-1].strip()
        if not sub:
            continue
        try:
            data = C.fetch_json(ctx, f"https://apply.workable.com/api/v1/widget/accounts/{sub}",
                                params={"details": "true"})
        except Exception:
            continue
        for j in (data.get("jobs", []) if isinstance(data, dict) else []):
            location = j.get("location") or {}
            loc_name = ", ".join(x for x in (location.get("city"), location.get("country")) if x) if isinstance(location, dict) else str(location)
            out.append(_norm(j.get("title"), sub, loc_name, j.get("url") or j.get("shortcode"), f"Workable/{sub}",
                             posted=(j.get("created_at") or "")[:10], type=j.get("type"),
                             description=C.strip_html(j.get("description", ""))))
    return _client_filter(out, q)


def _ashby(ctx, q, loc, limit, companies=None):
    out = []
    for org in companies or []:
        org = org.split(":")[-1].strip()
        if not org:
            continue
        try:
            data = C.fetch_json(ctx, f"https://api.ashbyhq.com/posting-api/job-board/{org}",
                                params={"includeCompensation": "true"})
        except Exception:
            continue
        for j in (data.get("jobs", []) if isinstance(data, dict) else []):
            comp = j.get("compensation") or {}
            out.append(_norm(j.get("title"), org, j.get("location"), j.get("jobUrl"), f"Ashby/{org}",
                             posted=(j.get("publishedAt") or "")[:10], type=j.get("employmentType"),
                             remote=bool(j.get("isRemote")), description=C.strip_html(j.get("descriptionHtml", "")),
                             salary=comp.get("compensationTierSummary") or ""))
    return _client_filter(out, q)


def _smartrecruiters(ctx, q, loc, limit, companies=None):
    out = []
    for company in companies or []:
        company = company.split(":")[-1].strip()
        if not company:
            continue
        try:
            data = C.fetch_json(ctx, f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                                params={"q": q or "", "limit": min(limit * 2, 100)})
        except Exception:
            continue
        for j in (data.get("content", []) if isinstance(data, dict) else []):
            location = j.get("location") or {}
            loc_name = ", ".join(x for x in (location.get("city"), location.get("country")) if x)
            out.append(_norm(j.get("name"), (j.get("company") or {}).get("name", company), loc_name,
                             f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}", f"SmartRecruiters/{company}",
                             posted=(j.get("releasedDate") or "")[:10], type=(j.get("typeOfEmployment") or {}).get("label", "")))
    return _client_filter(out, q)


API_ADAPTERS = {
    "remotive": _remotive, "arbeitnow": _arbeitnow, "jobicy": _jobicy, "himalayas": _himalayas,
    "remoteok": _remoteok, "weworkremotely": _weworkremotely, "lever": _lever, "greenhouse": _greenhouse,
    "workable": _workable, "ashby": _ashby, "smartrecruiters": _smartrecruiters,
}


# ---------------------------------------------------------------- HTML boards
BOARDS = {
    "linkedin": {
        "name": "LinkedIn", "regions": ["global", "zw", "za", "africa", "europe", "uk", "us"],
        "url": "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q}&location={loc}&start=0",
        "selectors": {"item": "li", "title": ".base-search-card__title", "company": ".base-search-card__subtitle",
                      "location": ".job-search-card__location", "link": "a.base-card__full-link", "date": "time"},
        "link_filter": "/jobs/view/",
    },
    "vacancymail": {
        "name": "VacancyMail Zimbabwe", "regions": ["zw"],
        "url": "https://vacancymail.co.zw/jobs/?search_keywords={q}&search_location={loc}",
        "selectors": {"item": "li.job_listing, article.job_listing, .job-list-item, article, li",
                      "title": "h3 a, h2 a, .job-title a, a", "company": ".company strong, .company, .job-company",
                      "location": ".location, .job-location", "link": "h3 a, h2 a, .job-title a, a", "date": "time, .date"},
        "link_filter": "/job/",
    },
    "myjobmag_zw": {
        "name": "MyJobMag Zimbabwe", "regions": ["zw"],
        "url": "https://www.myjobmag.co.zw/jobs/?q={q}",
        "selectors": {"item": "li.job-list-item, .job-list-item, article, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company, .job-company",
                      "location": ".location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/job/",
    },
    "careerjunction": {
        "name": "CareerJunction (South Africa)", "regions": ["za"],
        "url": "https://www.careerjunction.co.za/jobs/results/?keywords={q}&location={loc}",
        "selectors": {"item": "article, .job-result, li.job-result, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company, .job-company",
                      "location": ".location, .job-location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/jobs/",
    },
    "pnet": {
        "name": "PNet (South Africa)", "regions": ["za"],
        "url": "https://www.pnet.co.za/jobs/?keywords={q}&location={loc}",
        "selectors": {"item": "article, .job-result, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company, .job-company",
                      "location": ".location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/jobs/",
    },
    "careers24": {
        "name": "Careers24 (South Africa)", "regions": ["za", "africa"],
        "url": "https://www.careers24.com/jobs/?q={q}&l={loc}",
        "selectors": {"item": "article, .job-result, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company",
                      "location": ".location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/job/",
    },
    "jobmail": {
        "name": "Job Mail (South Africa)", "regions": ["za"],
        "url": "https://www.jobmail.co.za/jobs?q={q}&l={loc}",
        "selectors": {"item": "article, .job-result, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company",
                      "location": ".location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/job/",
    },
    "jobberman": {
        "name": "Jobberman (West Africa)", "regions": ["africa"],
        "url": "https://www.jobberman.com/jobs?q={q}&l={loc}",
        "selectors": {"item": "article, .job-list, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company",
                      "location": ".location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/jobs/",
    },
    "brightermonday": {
        "name": "BrighterMonday (East Africa)", "regions": ["africa"],
        "url": "https://www.brightermonday.co.ke/jobs?q={q}&l={loc}",
        "selectors": {"item": "article, .job-list, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company",
                      "location": ".location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/jobs/",
    },
    "eurojobs": {
        "name": "EuroJobs", "regions": ["europe", "uk", "eu"],
        "url": "https://www.eurojobs.com/jobs?q={q}&l={loc}",
        "selectors": {"item": "article, .job, li",
                      "title": "h2 a, h3 a, .job-title a, a", "company": ".company",
                      "location": ".location", "link": "h2 a, h3 a, a", "date": "time"},
        "link_filter": "/job/",
    },
    "indeed": {
        "name": "Indeed", "regions": ["global", "zw", "za", "africa", "europe", "uk", "us"],
        "url": "https://www.indeed.com/jobs?q={q}&l={loc}",
        "selectors": {"item": "div.job_seen_beacon, .jobsearch-ResultsList > li, article",
                      "title": "h2.jobTitle a, h2 a, a", "company": ".companyName, [data-testid=company-name]",
                      "location": ".companyLocation, [data-testid=text-location]", "link": "h2.jobTitle a, h2 a, a", "date": "span.date"},
        "link_filter": "/viewjob",
    },
}

REGION_SOURCES = {
    "zw": ["vacancymail", "myjobmag_zw", "linkedin", "careers24", "indeed"],
    "za": ["careerjunction", "pnet", "careers24", "jobmail", "linkedin"],
    "africa": ["jobberman", "brightermonday", "careers24", "linkedin"],
    "europe": ["arbeitnow", "jobicy", "himalayas", "remotive", "eurojobs", "linkedin"],
    "uk": ["arbeitnow", "jobicy", "himalayas", "remotive", "eurojobs", "linkedin"],
    "us": ["remotive", "remoteok", "himalayas", "jobicy", "linkedin"],
    "gulf": ["linkedin", "jobicy", "himalayas"],
    "remote": ["remotive", "remoteok", "himalayas", "jobicy", "weworkremotely", "linkedin"],
    "global": ["remotive", "remoteok", "himalayas", "jobicy", "arbeitnow", "linkedin"],
}

REGION_LOCATION = {"zw": "Zimbabwe", "za": "South Africa", "africa": "Africa", "europe": "Europe",
                   "uk": "United Kingdom", "us": "United States", "gulf": "United Arab Emirates",
                   "remote": "Remote", "global": "Remote"}


def normalise_region(value: str | None, default: str = "zw") -> str:
    v = (value or "").strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "zimbabwe": "zw", "zim": "zw", "harare": "zw", "bulawayo": "zw",
        "south africa": "za", "rsa": "za", "johannesburg": "za", "cape town": "za", "durban": "za", "pretoria": "za",
        "africa": "africa", "nigeria": "africa", "kenya": "africa", "ghana": "africa", "uganda": "africa",
        "tanzania": "africa", "zambia": "africa", "botswana": "africa", "namibia": "africa", "mozambique": "africa",
        "europe": "europe", "eu": "europe", "germany": "europe", "france": "europe", "netherlands": "europe",
        "spain": "europe", "italy": "europe", "portugal": "europe", "ireland": "europe", "belgium": "europe",
        "united kingdom": "uk", "uk": "uk", "england": "uk", "scotland": "uk", "wales": "uk", "london": "uk",
        "united states": "us", "usa": "us", "us": "us", "canada": "us", "america": "us",
        "uae": "gulf", "dubai": "gulf", "abu dhabi": "gulf", "saudi arabia": "gulf", "qatar": "gulf", "gulf": "gulf",
        "remote": "remote", "anywhere": "remote", "worldwide": "remote", "global": "global",
    }
    return aliases.get(v, v if v in REGION_SOURCES else default)


def _client_filter(jobs: list, q: str) -> list:
    if not q:
        return jobs
    words = [w for w in re.findall(r"[a-z0-9+#.]+", q.lower()) if len(w) > 2 and w not in STOPWORDS]
    if not words:
        return jobs
    out = []
    for j in jobs:
        blob = " ".join(str(j.get(k, "")) for k in ("title", "snippet", "tags", "description")).lower()
        if any(w in blob for w in words):
            out.append(j)
    return out


def _parse_listing(html_text: str, base_url: str, board: dict, limit: int) -> list:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ConnectorError("beautifulsoup4 is not installed on the server.")
    soup = BeautifulSoup(html_text or "", "html.parser")
    sel = board.get("selectors") or {}
    out, seen = [], set()

    def add(title, company, location, href, posted="", snippet=""):
        full = C.abs_url(base_url, href)
        if not full or full in seen:
            return
        title = C.collapse(title)
        if len(title) < 4 or len(title) > 160:
            return
        seen.add(full)
        out.append(_norm(title, company, location, full, board["name"], posted=C.collapse(posted),
                         snippet=C.collapse(snippet)[:400]))

    items = soup.select(sel.get("item", "li")) if sel.get("item") else []
    for item in items:
        link = item.select_one(sel["link"]) if sel.get("link") else None
        if not link or not link.get("href"):
            link = item.find("a", href=True)
        if not link:
            continue
        title_el = item.select_one(sel["title"]) if sel.get("title") else None
        title = title_el.get_text(" ") if title_el else link.get_text(" ")
        comp_el = item.select_one(sel["company"]) if sel.get("company") else None
        loc_el = item.select_one(sel["location"]) if sel.get("location") else None
        date_el = item.select_one(sel["date"]) if sel.get("date") else item.find("time")
        add(title, comp_el.get_text(" ") if comp_el else "", loc_el.get_text(" ") if loc_el else "",
            link.get("href"), date_el.get("datetime") or date_el.get_text(" ") if date_el else "",
            item.get_text(" "))

    if len(out) < 3:
        # generic fallback: any job-looking link on the page
        pat = board.get("link_filter") or r"/(job|jobs|vacanc|career|position|opening|apply)"
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            full = C.abs_url(base_url, href)
            if not re.search(pat, full, re.I):
                continue
            title = C.collapse(a.get_text(" "))
            if len(title) < 6 or len(title) > 160:
                continue
            parent = a.find_parent(["li", "article", "div", "tr"]) or a
            text = C.collapse(parent.get_text(" "))
            add(title, "", "", href, "", text)

    # JSON-LD ItemList (some boards expose this even when the HTML is JS-driven)
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") not in ("ItemList", ["ItemList"]):
                continue
            for el in node.get("itemListElement") or []:
                item = el.get("item") if isinstance(el, dict) else None
                if isinstance(item, dict):
                    add(item.get("name", ""), "", "", item.get("url", ""))
    return out[: max(limit * 3, 10)]


def _html_board(ctx, board: dict, q: str, loc: str, limit: int, use_browser: str) -> list:
    url = board["url"].format(q=C.quote(q or ""), loc=C.quote(loc or ""))
    html_text, _method = C.get_html(ctx, url, use_browser=use_browser)
    return _parse_listing(html_text, url, board, limit)


def default_sources(region: str) -> list:
    return list(REGION_SOURCES.get(region, REGION_SOURCES["global"]))


def _run_source(ctx, src: str, q: str, loc: str, limit: int, companies: list, use_browser: str) -> list:
    if src in API_ADAPTERS:
        return API_ADAPTERS[src](ctx, q, loc, limit, companies)
    board = BOARDS.get(src)
    if not board:
        raise ConnectorError(f"Unknown job board '{src}'.")
    return _html_board(ctx, board, q, loc, limit, use_browser)


def search_jobs(ctx, a):
    prof = C.load_profile(ctx)
    prefs = prof.get("preferences") or {}
    q = C.collapse(a.get("query") or ", ".join(C.as_list(prefs.get("titles"))) or prof.get("headline") or "")
    region = normalise_region(a.get("region") or prof.get("default_region") or "zw")
    loc = C.collapse(a.get("location") or (", ".join(C.as_list(prefs.get("locations"))) if not a.get("region") else "")
                     or REGION_LOCATION.get(region, ""))
    limit = C.clamp(a.get("limit", 20), 1, 100)
    sources = [s for s in (a.get("sources") or default_sources(region)) if isinstance(s, str)]
    companies = [str(c) for c in (a.get("companies") or []) if str(c).strip()]
    use_browser = str(a.get("use_browser", "auto")).lower()
    if use_browser not in ("auto", "always", "never"):
        use_browser = "auto"

    if not q and not companies:
        raise ConnectorError("Give a search query (or set preferred job titles in your profile first).")

    found, errors = [], []
    for src in sources:
        try:
            rows = _run_source(ctx, src, q, loc, limit, companies, use_browser)
            found.extend(rows)
            if not rows:
                errors.append(f"{src}: no results")
        except Exception as e:  # keep going: one blocked board must not kill the search
            errors.append(f"{src}: {type(e).__name__}: {str(e)[:180]}")

    # de-duplicate by URL, then keep the best `limit`
    dedup, seen = [], set()
    for j in found:
        key = (j.get("url") or "").split("?")[0].rstrip("/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        j["region"] = region
        dedup.append(j)

    scored = []
    for j in dedup:
        j["match_score"] = job_match_score(j, prof, q)
        scored.append(j)
    scored.sort(key=lambda j: (j.get("match_score", 0), j.get("posted", "")), reverse=True)
    kept = scored[:limit]
    for j in kept:
        C.upsert_job(ctx, j)

    lines = [f"Found {len(kept)} job(s) for \"{q}\" in {region.upper()}"
             + (f" near {loc}" if loc else "") + f" across {len(sources)} board(s). Saved to the tracker."]
    for j in kept:
        bits = [j.get("title", "?"), "at " + j["company"] if j.get("company") else "",
                "(" + j["location"] + ")" if j.get("location") else "",
                "· " + j["source"], "· " + j["posted"][:10] if j.get("posted") else "",
                "· match " + str(j.get("match_score", 0)) + "%"]
        lines.append(f"[{j['id']}] " + " ".join(x for x in bits if x) + f"\n    {j.get('url', '')}")
    if errors:
        lines.append("Board notes: " + "; ".join(errors[:8]))
    lines.append("Next: jobhunt_read_job with an id or url, then jobhunt_analyze_job.")
    payload = {"query": q, "region": region, "location": loc, "count": len(kept),
               "jobs": [{k: j.get(k) for k in ("id", "title", "company", "location", "url", "source",
                                               "posted", "salary", "remote", "match_score", "snippet")} for j in kept],
               "errors": errors}
    return ToolResult(text="\n".join(lines), data=json.dumps(payload, ensure_ascii=False),
                      untrusted="job-board listings")


# ---------------------------------------------------------------- reading adverts
def _walk_ld(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_ld(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_ld(v)


def _ld_location(node) -> str:
    loc = node.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, dict):
        addr = loc.get("address") or {}
        if isinstance(addr, list):
            addr = addr[0] if addr else {}
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            return ", ".join(str(p) for p in parts if p)
        if loc.get("name"):
            return str(loc["name"])
    return ""


def _ld_salary(node) -> str:
    sal = node.get("baseSalary")
    if isinstance(sal, dict):
        val = sal.get("value")
        if isinstance(val, dict):
            return _salary(val.get("minValue"), val.get("maxValue"), val.get("unitText") or sal.get("currency"))
        return _salary(val, sal.get("currency"))
    return ""


def _main_text(soup) -> str:
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"]):
        tag.decompose()
    best = None
    for sel in ("main", "article", "[class*=job-description]", "[id*=job-description]",
                "[class*=description]", "[id*=description]", ".content", "#content"):
        node = soup.select_one(sel)
        if node and len(node.get_text(" ", strip=True)) > 300:
            best = node
            break
    node = best or soup.body or soup
    text = node.get_text("\n")
    text = text.replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_job(html_text: str, url: str) -> dict:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ConnectorError("beautifulsoup4 is not installed on the server.")
    soup = BeautifulSoup(html_text or "", "html.parser")
    job = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except Exception:
            continue
        for node in _walk_ld(data):
            types = node.get("@type")
            if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
                job = {
                    "title": node.get("title") or node.get("name") or "",
                    "company": (node.get("hiringOrganization") or {}).get("name", "") if isinstance(node.get("hiringOrganization"), dict) else str(node.get("hiringOrganization") or ""),
                    "location": _ld_location(node),
                    "description": C.strip_html(node.get("description", "")),
                    "posted": node.get("datePosted", ""),
                    "valid_through": node.get("validThrough", ""),
                    "type": ", ".join(node.get("employmentType") or []) if isinstance(node.get("employmentType"), list) else str(node.get("employmentType") or ""),
                    "salary": _ld_salary(node),
                    "apply_url": node.get("url") or "",
                    "remote": str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE",
                }
                break
        if job:
            break

    title_meta = soup.find("meta", property="og:title")
    if not job.get("title"):
        h1 = soup.find("h1")
        job["title"] = C.collapse((h1.get_text(" ") if h1 else "") or (title_meta.get("content") if title_meta else "") or (soup.title.get_text() if soup.title else ""))
    if not job.get("company"):
        site = soup.find("meta", property="og:site_name")
        job["company"] = C.collapse(site.get("content") if site else "")
    if not job.get("description"):
        job["description"] = _main_text(soup)
    if not job.get("location"):
        node = soup.select_one("[class*=location], [data-testid*=location], [itemprop=jobLocation]")
        job["location"] = C.collapse(node.get_text(" ")) if node else ""
    desc = job.get("description", "")
    if not job.get("remote") and re.search(r"\b(fully remote|work from home|remote position|remote role)\b", desc, re.I):
        job["remote"] = True
    job["url"] = url
    job["apply_url"] = C.abs_url(url, job.get("apply_url") or "")
    job["id"] = C.job_id(url, job.get("title", ""), job.get("company", ""))
    return job


def read_job(ctx, a):
    jid = (a.get("job_id") or "").strip()
    url = (a.get("url") or "").strip()
    job = C.get_job(ctx, jid) if jid else None
    if job and not url:
        url = job.get("url", "")
    if not url:
        raise ConnectorError("Give a job id (from search) or the advert URL.")
    html_text, method = C.get_html(ctx, url, use_browser=str(a.get("use_browser", "auto")).lower())
    fresh = extract_job(html_text, url)
    merged = dict(job or {})
    merged.update({k: v for k, v in fresh.items() if v not in (None, "", [])})
    merged.setdefault("source", C.domain(url))
    merged = C.upsert_job(ctx, merged)
    desc = merged.get("description", "")
    lines = [f"{merged.get('title', '?')} — {merged.get('company', '')}".strip(" —"),
             f"Location: {merged.get('location') or 'not stated'} | Type: {merged.get('type') or 'n/a'} | "
             f"Salary: {merged.get('salary') or 'not stated'} | Posted: {merged.get('posted') or 'n/a'}",
             f"URL: {url}  (read via {method})", f"Tracker id: {merged['id']}"]
    if desc:
        lines.append("Description saved (" + str(len(desc)) + " chars). First lines:")
        lines.append(desc[:900] + ("..." if len(desc) > 900 else ""))
    return ToolResult(text="\n".join(lines), data=desc[:40000], untrusted=f"job advert on {C.domain(url)}")


# ---------------------------------------------------------------- keywords + analysis
def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9+#.'-]{2,}", (text or "").lower())


def extract_keywords(text: str) -> dict:
    low = " " + (text or "").lower() + " "
    skills = []
    for canon, aliases in SKILL_DB.items():
        if any((" " + al.strip() + " ") in low or re.search(r"\b" + re.escape(al.strip()) + r"\b", low) for al in aliases):
            skills.append(canon)
    soft = [s for s in SOFT_SKILLS if s in low]
    years = re.findall(r"(\d{1,2})\s*\+?\s*(?:-|to)?\s*\d{0,2}\s*(?:years|yrs)", low)
    seniority = "mid"
    for word, level in (("intern", "intern"), ("graduate", "graduate"), ("junior", "junior"),
                        ("senior", "senior"), ("lead", "lead"), ("principal", "principal"),
                        ("head of", "head"), ("director", "director"), ("manager", "manager")):
        if word in low:
            seniority = level
            break
    quals = []
    for pat in (r"bachelor[^.\n]{0,80}", r"degree[^.\n]{0,80}", r"diploma[^.\n]{0,80}", r"certificate[^.\n]{0,80}",
                r"master[^.\n]{0,80}", r"phd[^.\n]{0,80}", r"acca[^.\n]{0,60}", r"cima[^.\n]{0,60}",
                r"cfa[^.\n]{0,60}", r"registered[^.\n]{0,60}", r"honours[^.\n]{0,60}"):
        for m in re.finditer(pat, low):
            q = C.collapse(m.group(0))
            # keep just the qualification itself, not the rest of the sentence
            q = re.split(r"[,;]| and | or | with | plus |\.", q)[0].strip()
            words = q.split()
            if len(words) > 8:
                q = " ".join(words[:8])
            if not (len(q) >= 4 and len(q) < 70):
                continue
            if q in ("certificates", "certificate", "certified copies", "degree", "diploma"):
                continue
            if not re.search(r"bachelor|degree|diploma|certificate|master|phd|acca|cima|cfa|honours|registered", q):
                continue
            if q not in quals:
                quals.append(q)
    application = {}
    for label, needles in APPLICATION_HINTS.items():
        if any(n in low for n in needles):
            application[label] = True
    reqs = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = C.collapse(sent)
        if 20 < len(s) < 320 and re.search(r"\b(must|required|requirement|essential|minimum|qualification|proficien|experience in|knowledge of|ability to)\b", s, re.I):
            if s not in reqs:
                reqs.append(s)
        if len(reqs) >= 14:
            break
    freq = {}
    words = _words(text)
    for w in words:
        if w in STOPWORDS or len(w) < 4:
            continue
        freq[w] = freq.get(w, 0) + 1
    top_terms = [w for w, n in sorted(freq.items(), key=lambda kv: kv[1], reverse=True) if n >= 2][:35]
    red_flags = []
    for pat, label in ((r"commission\s*only", "commission-only pay"), (r"own\s+(reliable\s+)?(car|vehicle)", "own vehicle required"),
                       (r"unpaid", "unpaid role"), (r"no\s+experience\s+(necessary|required)", "no experience required"),
                       (r"must\s+relocate", "relocation expected"), (r"salary\s*:\s*negotiable", "salary negotiable"),
                       (r"urgent(ly)?\s+hiring", "urgent hiring")):
        if re.search(pat, low):
            red_flags.append(label)
    return {"skills": skills, "soft_skills": soft, "years_experience": years[0] if years else "",
            "seniority": seniority, "qualifications": quals[:8], "application_requirements": application,
            "requirements": reqs, "top_terms": top_terms, "red_flags": red_flags}


def job_match_score(job: dict, prof: dict, query: str = "") -> int:
    text = " ".join(str(job.get(k, "")) for k in ("title", "description", "snippet", "tags")).lower()
    have = {s.lower() for s in C.profile_skills(prof)}
    want = set()
    for canon, aliases in SKILL_DB.items():
        if any(a.strip() in text for a in aliases):
            want.add(canon.lower())
    score = 0
    if want:
        score += int(55 * len(have & want) / max(1, len(want)))
    titles = [t.lower() for t in C.as_list((prof.get("preferences") or {}).get("titles"))]
    if titles and any(t in (job.get("title") or "").lower() for t in titles):
        score += 20
    if query:
        qw = [w for w in _words(query) if w not in STOPWORDS]
        title = (job.get("title") or "").lower()
        if qw and sum(1 for w in qw if w in title) / len(qw) >= 0.5:
            score += 15
    prefs = prof.get("preferences") or {}
    if prefs.get("remote") and job.get("remote"):
        score += 5
    loc = (job.get("location") or "").lower()
    if loc and any(str(x).lower() in loc for x in C.as_list(prefs.get("locations"))):
        score += 10
    posted = str(job.get("posted") or "")[:10]
    if posted:
        try:
            if datetime.strptime(posted, "%Y-%m-%d") >= datetime.now() - timedelta(days=21):
                score += 5
        except ValueError:
            pass
    return max(0, min(100, score))


def analyze_job(ctx, a):
    job = C.get_job(ctx, a.get("job_id") or "") if a.get("job_id") else None
    if not job and a.get("url"):
        read_job(ctx, {"url": a["url"]})
        job = C.get_job(ctx, a["url"])
    desc = (a.get("job_description") or (job or {}).get("description") or "").strip()
    if not desc:
        raise ConnectorError("No job description found. Run jobhunt_read_job first, or pass job_description.")
    prof = C.load_profile(ctx)
    kw = extract_keywords(desc)
    match = job_match_score({"title": (job or {}).get("title", ""), "description": desc,
                             "location": (job or {}).get("location", ""), "remote": (job or {}).get("remote")},
                            prof, (job or {}).get("title", ""))
    result = dict(kw)
    result.update({"match_score": match, "job_id": (job or {}).get("id", ""), "title": (job or {}).get("title", ""),
                   "company": (job or {}).get("company", "")})

    if str(a.get("use_ai", True)).lower() not in ("false", "0", "no"):
        prompt = (
            "Analyse this job advert for one candidate. Return ONLY a JSON object with keys: "
            "summary (2 sentences), responsibilities (array of max 8 short strings), must_have (array), "
            "nice_to_have (array), ats_keywords (array of max 20 exact words/phrases an ATS would match), "
            "seniority, years_experience, qualifications (array), application_requirements (object with "
            "cover_letter, cv, certificates, references, portfolio booleans when stated), red_flags (array), "
            "match_score (0-100), gaps (array), tailoring_advice (array of max 5 short strings).\n\n"
            f"CANDIDATE PROFILE:\n{C.profile_summary_text(prof)[:4000]}\n\n"
            f"JOB ADVERT:\n{desc[:12000]}"
        )
        ai = C.json_from(C.llm(ctx, "You are a senior recruiter and ATS specialist. Output JSON only.", prompt, 1600))
        for k, v in ai.items():
            if v not in (None, "", [], {}):
                result[k] = v
        if isinstance(ai.get("match_score"), (int, float)):
            result["match_score"] = int(ai["match_score"])
        result["analysis_source"] = "ai+keywords"
    else:
        result["analysis_source"] = "keywords"

    if job:
        data = C.load_jobs(ctx)
        if job["id"] in data["jobs"]:
            data["jobs"][job["id"]]["analysis"] = result
            data["jobs"][job["id"]]["match_score"] = result.get("match_score", match)
            C.save_jobs(ctx, data)

    lines = [f"Analysis for {(job or {}).get('title') or 'the advert'}"
             + (f" at {(job or {}).get('company')}" if (job or {}).get("company") else ""),
             f"Match: {result.get('match_score', match)}% | Seniority: {result.get('seniority', 'n/a')} | "
             f"Experience: {result.get('years_experience') or 'not stated'}",
             "Must-have skills: " + (", ".join(result.get("skills", [])[:14]) or "not detected"),
             "ATS keywords: " + (", ".join(result.get("ats_keywords", [])[:18]) or "not detected"),
             "Application needs: " + (", ".join(k for k, v in (result.get("application_requirements") or {}).items() if v) or "CV only"),
             "Gaps: " + (", ".join(result.get("gaps", [])[:8]) or "none obvious"),
             "Tailoring advice: " + ("; ".join(result.get("tailoring_advice", [])[:5]) or "mirror the advert's wording in your summary and bullets")]
    return ToolResult(text="\n".join(lines), data=json.dumps(result, ensure_ascii=False, indent=2),
                      untrusted="job advert analysis")


def rank_jobs(ctx, a):
    prof = C.load_profile(ctx)
    data = C.load_jobs(ctx)
    status = a.get("status")
    rows = [j for j in data["jobs"].values() if not status or j.get("status") == status]
    for j in rows:
        j["match_score"] = max(int(j.get("match_score") or 0), job_match_score(j, prof, j.get("title", "")))
    rows.sort(key=lambda j: j.get("match_score", 0), reverse=True)
    rows = rows[: C.clamp(a.get("limit", 20), 1, 100)]
    for j in rows:
        data["jobs"][j["id"]] = j
    C.save_jobs(ctx, data)
    lines = [f"Top {len(rows)} job(s) by fit with your profile:"]
    for j in rows:
        lines.append(f"[{j['id']}] {j.get('match_score', 0)}% · {j.get('title')} — {j.get('company')} "
                     f"({j.get('location') or 'n/a'}) · {j.get('status', 'saved')}")
    return ToolResult(text="\n".join(lines) if rows else "No jobs in the tracker yet. Run jobhunt_search_jobs first.",
                      data=json.dumps([{k: j.get(k) for k in ("id", "title", "company", "location", "url",
                                                               "match_score", "status", "salary", "posted")} for j in rows],
                                      ensure_ascii=False),
                      untrusted="job tracker")


# ---------------------------------------------------------------- tracker
def save_job(ctx, a):
    job = {
        "title": a.get("title", ""), "company": a.get("company", ""), "url": a.get("url", ""),
        "location": a.get("location", ""), "source": a.get("source", ""), "salary": a.get("salary", ""),
        "notes": a.get("notes", ""), "status": a.get("status", "saved"), "remote": bool(a.get("remote", False)),
        "description": a.get("description", ""),
    }
    if not job["title"] or not job["url"]:
        raise ConnectorError("title and url are required.")
    saved = C.upsert_job(ctx, job)
    return ToolResult(f"Saved [{saved['id']}] {saved['title']} at {saved.get('company') or '?'} (status: {saved['status']}).",
                      data=json.dumps(saved, ensure_ascii=False, indent=2))


def list_jobs(ctx, a):
    data = C.load_jobs(ctx)
    status = (a.get("status") or "").strip()
    query = (a.get("query") or "").lower()
    rows = []
    for j in data["jobs"].values():
        if status and j.get("status") != status:
            continue
        if query and query not in json.dumps(j).lower():
            continue
        rows.append(j)
    rows.sort(key=lambda j: j.get("last_seen", 0), reverse=True)
    rows = rows[: C.clamp(a.get("limit", 30), 1, 200)]
    if not rows:
        return "No jobs match. Run jobhunt_search_jobs to find some."
    lines = [f"{len(rows)} job(s):"]
    for j in rows:
        lines.append(f"[{j['id']}] {j.get('status', 'saved'):<10} {j.get('title')} — {j.get('company')} "
                     f"({j.get('location') or 'n/a'}) {('· applied ' + j['applied_at'][:10]) if j.get('applied_at') else ''}")
    return ToolResult("\n".join(lines), data=json.dumps(rows, ensure_ascii=False))


def update_job(ctx, a):
    jid = (a.get("job_id") or a.get("url") or "").strip()
    job = C.get_job(ctx, jid) if jid else None
    if not job:
        raise ConnectorError("No such job in the tracker. Use jobhunt_list_jobs to see ids.")
    data = C.load_jobs(ctx)
    row = data["jobs"][job["id"]]
    for k in ("status", "notes", "salary", "title", "company", "location", "applied_at", "remind_at"):
        if a.get(k) not in (None, ""):
            row[k] = a[k]
    if a.get("documents"):
        row["documents"] = C.deep_merge(row.get("documents") or {}, a["documents"])
    if a.get("status") == "applied" and not row.get("applied_at"):
        row["applied_at"] = C.today_str()
    row["last_seen"] = C.now_ts()
    C.save_jobs(ctx, data)
    return ToolResult(f"Updated [{row['id']}] {row.get('title')} — status {row.get('status')}, notes saved.",
                      data=json.dumps(row, ensure_ascii=False, indent=2))
