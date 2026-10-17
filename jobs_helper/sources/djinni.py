"""Djinni: RSS for search (category based), vacancy page JSON-LD for details.

Feed: https://djinni.co/jobs/rss/?primary_keyword=…&salary=…&employment=remote  (max 100 items, `page` ignored)
An unknown primary_keyword silently returns ALL jobs, so keywords are validated against the
category list published in the feed channel itself.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

import feedparser
from bs4 import BeautifulSoup

from jobs_helper import config
from jobs_helper.extract import (feed_date, guess_english, guess_experience_years, guess_seniority, guess_work_mode,
                                 html_to_text, iso_date, tokenize)
from jobs_helper.http import FetchError, fetch, fetch_text
from jobs_helper.models import Job, SearchQuery
from jobs_helper.sources.base import Source, SourceError, norm, rank

FEED_URL = "https://djinni.co/jobs/rss/"
URL_RE = re.compile(r"djinni\.co/jobs/(\d+-[^/?#]+)")
ALIASES = {"frontend": "JavaScript", "front-end": "JavaScript", "js": "JavaScript", "react": "React.js",
           "node": "Node.js", "nodejs": "Node.js", "vue": "Vue.js", "angular": "Angular", "ml": "ML AI",
           "ai": "ML AI", "go": "Golang", "c#": "C# / .NET", "dotnet": "C# / .NET", "fullstack": "Fullstack"}


class DjinniSource(Source):
    name = "djinni"

    def __init__(self, enrich_results: bool = True):
        self.enrich_results = enrich_results   # fetch pages of returned jobs to get company/experience
        self._categories: list[str] | None = None

    # ------------------------------------------------------------ categories
    def categories(self) -> list[str]:
        if self._categories is None:
            try:
                feed = feedparser.parse(fetch(FEED_URL, ttl=7 * 86400))
                self._categories = [t["term"] for t in feed.feed.get("tags", [])]
            except FetchError:
                self._categories = []
        return self._categories

    def match_category(self, keywords: str) -> str | None:
        cats = self.categories()
        by_norm = {norm(c): c for c in cats}
        for t in tokenize(keywords):
            if norm(t) in by_norm:
                return by_norm[norm(t)]
            alias = ALIASES.get(t)
            if alias and (alias in cats or not cats):
                return alias
        return None

    # ------------------------------------------------------------ search
    def search(self, q: SearchQuery) -> list[Job]:
        category = self.match_category(q.keywords)
        params = {"primary_keyword": category, "salary": q.min_salary, "employment": "remote" if q.remote else None}
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        feed = feedparser.parse(fetch(f"{FEED_URL}?{qs}" if qs else FEED_URL, ttl=config.FEED_TTL_S))
        jobs = [self._from_entry(e) for e in feed.entries if URL_RE.search(e.link)]
        ranked = rank(jobs, q, require_match=category is None)
        if self.enrich_results:
            enriched = []
            for j in ranked:
                try:
                    enriched.append(self.get(j.id))
                except SourceError:
                    enriched.append(j)
            ranked = rank(enriched, q, require_match=category is None)  # filters may now apply
        return ranked

    def _from_entry(self, e) -> Job:
        key = URL_RE.search(e.link).group(1)
        desc = html_to_text(e.get("summary"))
        title = e.title.strip()
        return Job(
            id=f"djinni:{key}", source="djinni", url=f"https://djinni.co/jobs/{key}/", title=re.sub(r"\s+", " ", title),
            description=desc, tags=[t["term"] for t in e.get("tags", []) if t.get("term")],
            work_mode=guess_work_mode(desc), seniority=guess_seniority(title),
            experience_years=guess_experience_years(desc), english=guess_english(desc),
            published_at=feed_date(e), apply_type="on_site",
        )

    # ------------------------------------------------------------ details
    def get(self, job_id: str) -> Job:
        key = self.key(job_id)
        if not re.fullmatch(r"\d+-[^/\s]+", key):
            raise SourceError("invalid_job_id", f"'{job_id}' is malformed.", "Djinni ids look like 'djinni:<number>-<slug>'.")
        url = f"https://djinni.co/jobs/{key}/"
        try:
            html = fetch_text(url)
        except FetchError as e:
            if e.status == 404:
                raise SourceError("job_not_found", f"{job_id} does not exist or was removed.", "Use search_jobs to find current vacancies.")
            raise SourceError("source_unavailable", f"Djinni is not reachable: {e}", "Retry later.")
        return self._from_page(job_id, url, html)

    def _from_page(self, job_id: str, url: str, html: str) -> Job:
        s = BeautifulSoup(html, "lxml")
        jp = _jobposting(s) or {}
        desc = html_to_text(jp.get("description")) or html_to_text(str(s.select_one(".job-post-description") or ""))
        title = re.sub(r"\s+", " ", jp.get("title") or (s.h1.get_text(" ", strip=True) if s.h1 else "")).strip()
        months = (jp.get("experienceRequirements") or {}).get("monthsOfExperience")
        loc_type = jp.get("jobLocationType")
        page_text = s.get_text(" ", strip=True)
        base = jp.get("baseSalary") or {}
        value = base.get("value") if isinstance(base, dict) else None
        lo = hi = None
        if isinstance(value, dict):
            lo, hi = value.get("minValue"), value.get("maxValue")
        countries = [a.get("name") for a in _as_list(jp.get("applicantLocationRequirements")) if isinstance(a, dict)]
        form = s.select_one("form.js-inbox-reply-form")
        return Job(
            id=job_id, source="djinni", url=url, title=title,
            company=(jp.get("hiringOrganization") or {}).get("name"), description=desc,
            location=", ".join(c for c in countries if c) or None,
            work_mode="remote" if loc_type == "TELECOMMUTE" else guess_work_mode(desc),
            salary_min=int(lo) if lo else None, salary_max=int(hi) if hi else None,
            seniority=guess_seniority(title, desc[:400]),
            experience_years=round(months / 12, 1) if months else guess_experience_years(desc),
            english=guess_english(page_text), tags=[c for c in [jp.get("category")] if c],
            published_at=iso_date(jp.get("datePosted")), valid_through=iso_date(jp.get("validThrough")),
            apply_type="on_site" if form is not None or jp.get("directApply") else "unknown", apply_url=url,
            details_loaded=True,
        )


def _jobposting(soup) -> dict | None:
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except json.JSONDecodeError:
            continue
        for item in _as_list(data.get("@graph", data) if isinstance(data, dict) else data):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _as_list(x):
    return x if isinstance(x, list) else ([x] if x else [])
