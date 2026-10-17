"""Vacancies the candidate brings themselves: a link (LinkedIn, Work.ua, a company career page, an ATS) or pasted text.

LinkedIn: one public job page per link the candidate pastes (no login, no search, no crawling); applying through
LinkedIn (Easy Apply) stays in the candidate's own account. Other sites: JSON-LD JobPosting when present, otherwise
the page text. Every read vacancy is kept locally under a stable id ('li:<id>' or 'ext:<hash>') so get_job,
review_cv and apply work with it like with board vacancies.
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from jobs_helper import config
from jobs_helper.extract import (guess_english, guess_experience_years, guess_seniority, guess_work_mode, html_to_text,
                                 iso_date, parse_salary)
from jobs_helper.http import FetchError, fetch_text
from jobs_helper.models import Job
from jobs_helper.sources.base import Source, SourceError

LI_ID = re.compile(r"(?:/jobs/view/(?:[^/?]*-)?(\d{6,})|currentJobId=(\d{6,})|jobPosting:(\d{6,}))")


def store_path():
    return config.HOME / "external_jobs.json"


def _load() -> dict:
    try:
        return json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(job: Job) -> Job:
    data = _load()
    data[job.id] = job.model_dump()
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return job


def linkedin_job_id(url: str) -> str | None:
    m = LI_ID.search(url or "")
    return next((g for g in m.groups() if g), None) if m else None


def read_linkedin(job_id: str) -> Job:
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    try:
        html = fetch_text(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}", ttl=6 * 3600)
    except FetchError as e:
        raise SourceError("job_not_found" if e.status in (404, 410) else "source_unavailable",
                          f"LinkedIn job {job_id} could not be read ({e.status or 'network'}).",
                          "Open the link and paste the vacancy text instead (read_vacancy with text).")
    s = BeautifulSoup(html, "lxml")
    t = lambda sel: (s.select_one(sel).get_text(" ", strip=True) if s.select_one(sel) else None)  # noqa: E731
    title = t(".top-card-layout__title") or t(".topcard__title")
    if not title:
        raise SourceError("job_not_found", f"LinkedIn job {job_id} has no public content (closed or private).",
                          "Paste the vacancy text instead.")
    desc = html_to_text(str(s.select_one(".show-more-less-html__markup") or ""))
    criteria = {li.select_one(".description__job-criteria-subheader").get_text(strip=True):
                li.select_one(".description__job-criteria-text").get_text(strip=True)
                for li in s.select(".description__job-criteria-item")
                if li.select_one(".description__job-criteria-subheader") and li.select_one(".description__job-criteria-text")}
    apply_type, apply_url = "linkedin", url
    code = s.find("code", id="applyUrl")
    if code and code.string:                     # "Apply on company website": the real form is on the employer's site
        raw = code.string.strip().strip('"<!->')
        target = parse_qs(urlparse(raw).query).get("url", [None])[0]
        if target:
            apply_type, apply_url = "external", unquote(target)
    location = t(".topcard__flavor--bullet")
    seniority_raw = criteria.get("Seniority level", "")
    return _save(Job(
        id=f"li:{job_id}", source="linkedin", url=url, title=title, company=t("a.topcard__org-name-link") or t(".topcard__org-name"),
        description=desc, location=location, cities=[location.split(",")[0]] if location else [],
        work_mode=guess_work_mode(f"{location or ''} {desc[:1500]}"),
        seniority=guess_seniority(title, seniority_raw), experience_years=guess_experience_years(desc),
        english=guess_english(desc), tags=[v for k, v in criteria.items() if k in ("Employment type", "Job function")],
        apply_type=apply_type, apply_url=apply_url, details_loaded=True,
    ))


def read_page(url: str) -> Job:
    try:
        html = fetch_text(url, ttl=6 * 3600)
    except FetchError as e:
        raise SourceError("job_not_found" if e.status in (404, 410) else "source_unavailable",
                          f"The page could not be opened ({e.status or 'network'}).", "Paste the vacancy text instead.")
    s = BeautifulSoup(html, "lxml")
    jp = None
    for sc in s.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except json.JSONDecodeError:
            continue
        for item in (data if isinstance(data, list) else data.get("@graph", [data])):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                jp = item
    jid = "ext:" + hashlib.sha1(url.encode()).hexdigest()[:10]
    if jp:
        desc = html_to_text(jp.get("description"))
        org = jp.get("hiringOrganization") or {}
        title = re.sub(r"\s+", " ", jp.get("title") or "").strip()
        salary = jp.get("baseSalary") or {}
        lo = hi = None
        if isinstance(salary, dict) and isinstance(salary.get("value"), dict):
            lo, hi = salary["value"].get("minValue"), salary["value"].get("maxValue")
    else:
        for tag in s(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        main = s.select_one("main, article, [class*='vacancy'], [class*='job']") or s.body or s
        desc = html_to_text(str(main))[:12000]
        title = (s.h1.get_text(" ", strip=True) if s.h1 else (s.title.get_text(strip=True) if s.title else url))[:160]
        org, lo, hi = {}, *parse_salary(desc[:3000])
    if len(desc) < 200:
        raise SourceError("vacancy_not_readable", "The page has too little readable text (probably rendered by JavaScript).",
                          "Paste the vacancy text instead.")
    return _save(Job(
        id=jid, source="external", url=url, title=title or "Vacancy", company=(org.get("name") if isinstance(org, dict) else None),
        description=desc, work_mode=guess_work_mode(desc[:2000]), salary_min=int(lo) if lo else None,
        salary_max=int(hi) if hi else None, seniority=guess_seniority(title, desc[:400]),
        experience_years=guess_experience_years(desc), english=guess_english(desc),
        published_at=iso_date(jp.get("datePosted")) if jp else None, valid_through=iso_date(jp.get("validThrough")) if jp else None,
        apply_type="external", apply_url=url, details_loaded=True,
    ))


def read_text(text: str, title: str | None = None, company: str | None = None, url: str | None = None) -> Job:
    text = text.strip()
    if len(text) < 150:
        raise SourceError("invalid_argument", "The vacancy text is too short.", "Paste the whole vacancy (title, requirements).", True)
    first = next((line.strip() for line in text.splitlines() if line.strip()), "Vacancy")
    jid = "ext:" + hashlib.sha1(text.encode()).hexdigest()[:10]
    return _save(Job(
        id=jid, source="external", url=url or "", title=(title or first)[:160], company=company, description=text[:12000],
        work_mode=guess_work_mode(text[:2000]), seniority=guess_seniority(title or first, text[:400]),
        experience_years=guess_experience_years(text), english=guess_english(text),
        apply_type="external" if url else "unknown", apply_url=url, details_loaded=True,
    ))


def read_vacancy(url: str | None = None, text: str | None = None, title: str | None = None, company: str | None = None) -> Job:
    if url and (jid := linkedin_job_id(url)) and "linkedin." in url:
        return read_linkedin(jid)
    if text:
        return read_text(text, title, company, url)
    if url:
        return read_page(url)
    raise SourceError("invalid_argument", "Pass a vacancy link or its text.", "E.g. a LinkedIn job link.", True)


class ExternalSource(Source):
    """Lookup for vacancies read earlier (li:<id>, ext:<hash>)."""
    name = "external"

    def search(self, q):   # external vacancies are not searchable boards
        return []

    def get(self, job_id: str) -> Job:
        data = _load()
        if job_id in data:
            return Job(**data[job_id])
        if job_id.startswith("li:") and job_id[3:].isdigit():
            return read_linkedin(job_id[3:])
        raise SourceError("job_not_found", f"{job_id} is not among the vacancies read in this profile.",
                          "Read it again with read_vacancy (link or text).")
