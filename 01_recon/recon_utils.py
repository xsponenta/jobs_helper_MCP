"""Helpers for the recon notebooks: polite HTTP with disk cache, parsers, ATS detection."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "samples" / "raw"
SAMPLES_DIR = HERE / "samples"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
MIN_DELAY = 1.0  # seconds between real requests to the same host

_last_hit: dict[str, float] = {}
_client = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "uk,en;q=0.8"}, timeout=25)


def _wait(host: str) -> None:
    dt = time.monotonic() - _last_hit.get(host, 0)
    if dt < MIN_DELAY:
        time.sleep(MIN_DELAY - dt)
    _last_hit[host] = time.monotonic()


def fetch(url: str, ttl: int = 6 * 3600) -> str:
    """GET with redirects, rate limit per host and an on-disk cache."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_text()
    _wait(urlparse(url).netloc)
    r = _client.get(url, follow_redirects=True)
    r.raise_for_status()
    path.write_text(r.text)
    return r.text


def resolve_redirects(url: str, max_hops: int = 6) -> list[str]:
    """Follow redirects hop by hop without downloading bodies; returns the chain."""
    chain = [url]
    for _ in range(max_hops):
        _wait(urlparse(chain[-1]).netloc)
        try:
            r = _client.head(chain[-1], follow_redirects=False)
            if r.status_code in (405, 403):
                r = _client.get(chain[-1], follow_redirects=False)
        except httpx.HTTPError:
            break
        loc = r.headers.get("location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            break
        if loc.startswith("/"):
            p = urlparse(chain[-1])
            loc = f"{p.scheme}://{p.netloc}{loc}"
        chain.append(loc)
    return chain


def save_sample(name: str, data) -> Path:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    path = SAMPLES_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return path


# ---------------------------------------------------------------- ATS detection

ATS_PATTERNS = [
    ("greenhouse", r"(^|\.)greenhouse\.io$|^grnh\.se$"),
    ("lever", r"(^|\.)lever\.co$"),
    ("workable", r"(^|\.)workable\.com$"),
    ("bamboohr", r"(^|\.)bamboohr\.com$"),
    ("peopleforce", r"(^|\.)peopleforce\.io$"),
    ("recruitee", r"(^|\.)recruitee\.com$"),
    ("smartrecruiters", r"(^|\.)smartrecruiters\.com$"),
    ("teamtailor", r"(^|\.)teamtailor\.com$"),
    ("breezy", r"(^|\.)breezy\.hr$"),
    ("ashby", r"(^|\.)ashbyhq\.com$"),
    ("personio", r"(^|\.)personio\.(de|com)$"),
    ("workday", r"(^|\.)myworkdayjobs\.com$"),
    ("jobvite", r"(^|\.)jobvite\.com$"),
    ("comeet", r"(^|\.)comeet\.(com|co)$"),
    ("hirehive", r"(^|\.)hirehive\.com$"),
    ("zoho_recruit", r"(^|\.)zohorecruit\.(com|eu)$"),
    ("google_forms", r"^docs\.google\.com$|^forms\.gle$"),
    ("linkedin", r"(^|\.)linkedin\.com$"),
    ("telegram", r"^t\.me$|^telegram\.me$"),
    ("djinni", r"(^|\.)djinni\.co$"),
]


def classify_ats(url: str) -> str:
    if url.startswith("mailto:"):
        return "email"
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for name, pat in ATS_PATTERNS:
        if re.search(pat, host):
            if name == "google_forms" and "/forms" not in url and host != "forms.gle":
                continue
            return name
    return "company_site" if host else "unknown"


# ---------------------------------------------------------------- parsers

SALARY_RE = re.compile(r"(?:від\s*|до\s*)?\$\s?(\d[\d\s]*)(?:\s*[–-]\s*(\d[\d\s]*))?")


def parse_salary(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    m = SALARY_RE.search(text)
    if not m:
        return None, None
    lo = int(re.sub(r"\D", "", m.group(1)))
    hi = int(re.sub(r"\D", "", m.group(2))) if m.group(2) else lo
    if text.strip().startswith("до"):
        lo = None
    return lo, hi


def parse_dou_title(title: str) -> dict:
    """'Middle Python Dev в Acme, $2200–2800, Київ, віддалено' -> parts."""
    title = title.replace("&amp;", "&")
    head, sep, tail = title.rpartition(" в ")
    if not sep:
        return {"title": title}
    parts = [p.strip() for p in tail.split(",")]
    company, rest = parts[0], parts[1:]
    salary = next((p for p in rest if "$" in p), None)
    places = [p for p in rest if p != salary]
    lo, hi = parse_salary(salary)
    return {
        "title": head.strip(), "company": company, "salary_raw": salary,
        "salary_min": lo, "salary_max": hi,
        "remote": "віддалено" in places, "abroad": "за кордоном" in places,
        "cities": [p for p in places if p not in ("віддалено", "за кордоном")],
    }


def parse_dou_page(html: str) -> dict:
    s = BeautifulSoup(html, "lxml")
    v = s.select_one(".b-vacancy") or s
    reply = v.select_one("a.replied-external") or v.select_one(".reply a, a[href='#apply']")
    href = reply.get("href") if reply else None
    return {
        "title": _text(v.select_one("h1")),
        "company": _text(s.select_one(".b-compinfo .l-n a")),
        "salary": _text(v.select_one(".salary")),
        "place": _text(v.select_one(".place")),
        "date": _text(v.select_one(".date")),
        "description": _text(v.select_one(".b-typo.vacancy-section")),
        "apply_type": "external" if reply and "replied-external" in (reply.get("class") or []) else ("on_site" if reply else "none"),
        "apply_href": href,
    }


def parse_jsonld_jobposting(html: str) -> dict | None:
    s = BeautifulSoup(html, "lxml")
    for sc in s.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                return it
    return None


def parse_djinni_page(html: str) -> dict:
    jp = parse_jsonld_jobposting(html) or {}
    s = BeautifulSoup(html, "lxml")
    form = s.select_one("form.js-inbox-reply-form")
    fields = []
    if form:
        for el in form.find_all(["input", "textarea", "select"]):
            fields.append({"tag": el.name, "type": el.get("type"), "name": el.get("name"), "required": el.has_attr("required")})
    exp = (jp.get("experienceRequirements") or {}).get("monthsOfExperience")
    sal = jp.get("estimatedSalary") or jp.get("baseSalary")
    return {
        "title": jp.get("title"),
        "company": (jp.get("hiringOrganization") or {}).get("name"),
        "date_posted": jp.get("datePosted"), "valid_through": jp.get("validThrough"),
        "employment_type": jp.get("employmentType"), "location_type": jp.get("jobLocationType"),
        "applicant_location": jp.get("applicantLocationRequirements"),
        "experience_months": exp, "salary": sal, "category": jp.get("category"),
        "direct_apply": jp.get("directApply"), "apply_form_fields": fields,
        "jsonld_keys": sorted(jp.keys()),
    }


def parse_hm_page(html: str) -> dict:
    s = BeautifulSoup(html, "lxml")
    meta = s.select_one(".single-vacancy__meta")
    info = s.select_one(".single-vacancy__info")
    apply_btn = s.select_one("a.js_job_click")
    return {
        "title": _text(s.select_one(".single-vacancy__title h1, h1")),
        "company": _text(s.select_one(".single-vacancy__meta-company")),
        "place": _text(s.select_one(".single-vacancy__meta-place")),
        "meta": _text(meta), "info": _text(info),
        "partner_source": "З партнерських джерел" in s.get_text(" ", strip=True),
        "description": _text(s.select_one(".single-vacancy__text")),
        "apply_href": apply_btn.get("href") if apply_btn else None,
        "apply_requires_login": bool(apply_btn and "/profile/login" in (apply_btn.get("href") or "")),
    }


def _text(el) -> str | None:
    return el.get_text(" ", strip=True) if el else None


# ---------------------------------------------------------------- form schema (Playwright)

# Runs inside the page; returns visible form controls with their human-readable labels.
FORM_SCHEMA_JS = r"""
() => {
  const visible = el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return (r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none') || el.type === 'file'; };
  const labelOf = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText.trim(); }
    const wrap = el.closest('label'); if (wrap) return wrap.innerText.trim();
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const lb = el.getAttribute('aria-labelledby'); if (lb) { const n = document.getElementById(lb); if (n) return n.innerText.trim(); }
    const field = el.closest('.field, .form-group, .application-question, [class*="field"], [class*="question"]');
    if (field) { const l = field.querySelector('label, legend, .label, [class*="label"]'); if (l) return l.innerText.trim(); }
    return el.placeholder || el.name || '';
  };
  const out = [];
  document.querySelectorAll('input, textarea, select').forEach(el => {
    if (['hidden', 'submit', 'button', 'search'].includes(el.type)) return;
    if (!visible(el)) return;
    out.push({
      tag: el.tagName.toLowerCase(), type: el.type || null, name: el.name || el.id || null,
      label: labelOf(el).slice(0, 120),
      required: el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(labelOf(el)),
      options: el.tagName === 'SELECT' ? [...el.options].slice(0, 8).map(o => o.text.trim()) : undefined,
      accept: el.type === 'file' ? (el.accept || null) : undefined,
    });
  });
  const captcha = !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], .g-recaptcha, .h-captcha, [data-sitekey]');
  return { url: location.href, title: document.title, fields: out, captcha };
}
"""
