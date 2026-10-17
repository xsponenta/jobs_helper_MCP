"""DOU: official RSS for search, vacancy page for details and apply type.

Feed: https://jobs.dou.ua/vacancies/feeds/?category=…&search=…&remote&city=…  (max 25 items, no paging)
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

import feedparser
from bs4 import BeautifulSoup

from jobs_helper import config
from jobs_helper.extract import (feed_date, guess_english, guess_experience_years, guess_seniority, guess_work_mode,
                                 html_to_text, parse_salary, parse_ua_date, tokenize)
from jobs_helper.http import FetchError, fetch, fetch_text, resolve_redirects
from jobs_helper.models import Job, SearchQuery
from jobs_helper.sources.base import Source, SourceError, norm, rank

FEED_URL = "https://jobs.dou.ua/vacancies/feeds/"
URL_RE = re.compile(r"jobs\.dou\.ua/companies/([^/]+)/vacancies/(\d+)")

FALLBACK_CATEGORIES = [
    ".NET", "AI/ML", "Analyst", "Android", "Big Data", "C++", "Data Engineer", "Data Science", "DBA", "Design",
    "DevOps", "Embedded", "Flutter", "Front End", "Golang", "iOS/macOS", "Java", "Node.js", "PHP", "Product Manager",
    "Project Manager", "Python", "QA", "React Native", "Ruby", "Rust", "Scala", "Security", "SysAdmin", "Unity",
]
ALIASES = {
    "frontend": "Front End", "front-end": "Front End", "react": "Front End", "angular": "Front End",
    "vue": "Front End", "javascript": "Front End", "typescript": "Front End", "js": "Front End",
    "ml": "AI/ML", "ai": "AI/ML", "go": "Golang", "node": "Node.js", "nodejs": "Node.js",
    "ios": "iOS/macOS", "swift": "iOS/macOS", "c#": ".NET", "dotnet": ".NET", "tester": "QA",
}


class DouSource(Source):
    name = "dou"

    def __init__(self):
        self._categories: list[str] | None = None

    # ------------------------------------------------------------ categories
    def categories(self) -> list[str]:
        if self._categories is None:
            try:
                soup = BeautifulSoup(fetch_text("https://jobs.dou.ua/vacancies/", ttl=7 * 86400), "lxml")
                sel = soup.find("select", attrs={"name": "category"})
                cats = [o.get("value") for o in sel.find_all("option") if o.get("value")] if sel else []
            except FetchError:
                cats = []
            self._categories = cats or FALLBACK_CATEGORIES
        return self._categories

    def match_category(self, keywords: str) -> tuple[str | None, list[str]]:
        """Returns (category, remaining tokens)."""
        tokens = tokenize(keywords)
        by_norm = {norm(c): c for c in self.categories()}
        for i, t in enumerate(tokens):
            cat = by_norm.get(norm(t)) or ALIASES.get(t)
            if cat:
                return cat, tokens[:i] + tokens[i + 1:]
        return None, tokens

    # ------------------------------------------------------------ search
    def _feed(self, **params) -> list[Job]:
        qs = urlencode({k: v for k, v in params.items() if v not in (None, True, False)})
        flags = "&".join(k for k, v in params.items() if v is True)
        url = f"{FEED_URL}?{qs}" + (f"&{flags}" if flags else "")
        feed = feedparser.parse(fetch(url, ttl=config.FEED_TTL_S))
        return [self._from_entry(e) for e in feed.entries if URL_RE.search(e.link)]

    def search(self, q: SearchQuery) -> list[Job]:
        category, rest = self.match_category(q.keywords)
        common = {"remote": True if q.remote else None, "city": q.city}
        attempts = []
        if category and rest:
            attempts.append({"category": category, "search": " ".join(rest)})
        if category:
            attempts.append({"category": category})
        attempts.append({"search": q.keywords})
        seen: dict[str, Job] = {}
        for params in attempts:
            for job in self._feed(**params, **common):
                seen.setdefault(job.id, job)
            if len(seen) >= q.limit:
                break
        return rank(list(seen.values()), q, require_match=category is None)

    def _from_entry(self, e) -> Job:
        m = URL_RE.search(e.link)
        slug, vid = m.group(1), m.group(2)
        title, company, salary_raw, places = _split_title(e.title)
        lo, hi = parse_salary(salary_raw)
        desc = html_to_text(e.get("summary"))
        remote = "віддалено" in places
        cities = [p for p in places if p not in ("віддалено", "за кордоном")]
        return Job(
            id=f"dou:{slug}/{vid}", source="dou", url=f"https://jobs.dou.ua/companies/{slug}/vacancies/{vid}/",
            title=title, company=company, description=desc, location=", ".join(places) or None, cities=cities,
            work_mode="remote" if remote else (guess_work_mode(desc) if not cities else "onsite"),
            salary_min=lo, salary_max=hi, seniority=guess_seniority(title),
            experience_years=guess_experience_years(desc), english=guess_english(desc),
            published_at=feed_date(e),
        )

    # ------------------------------------------------------------ details
    def get(self, job_id: str) -> Job:
        key = self.key(job_id)
        if not re.fullmatch(r"[^/\s]+/\d+", key):
            raise SourceError("invalid_job_id", f"'{job_id}' is malformed.", "DOU ids look like 'dou:<company-slug>/<number>'.")
        slug, vid = key.split("/")
        url = f"https://jobs.dou.ua/companies/{slug}/vacancies/{vid}/"
        try:
            html = fetch_text(url)
        except FetchError as e:
            if e.status == 404:
                raise SourceError("job_not_found", f"{job_id} does not exist or was removed.", "Use search_jobs to find current vacancies.")
            raise SourceError("source_unavailable", f"DOU is not reachable: {e}", "Retry later.")
        return self._from_page(job_id, url, html)

    def _from_page(self, job_id: str, url: str, html: str) -> Job:
        s = BeautifulSoup(html, "lxml")
        v = s.select_one(".b-vacancy") or s
        title = _text(v.select_one("h1")) or ""
        place = _text(v.select_one(".place")) or ""
        places = [p.strip() for p in place.split(",") if p.strip()]
        salary = _text(v.select_one(".salary"))
        lo, hi = parse_salary(salary)
        desc = html_to_text(str(v.select_one(".b-typo.vacancy-section") or ""))
        reply = v.select_one("a.replied-external") or v.select_one("a[href='#apply']")
        if reply is None:
            apply_type, apply_url = "unknown", None
        elif "replied-external" in (reply.get("class") or []):
            apply_type, apply_url = "external", resolve_redirects(reply["href"])
        else:
            apply_type, apply_url = "on_site", url + "#apply"
        cities = [p for p in places if p not in ("віддалено", "за кордоном")]
        return Job(
            id=job_id, source="dou", url=url, title=title, company=_text(s.select_one(".b-compinfo .l-n a")),
            description=desc, location=place or None, cities=cities,
            work_mode="remote" if "віддалено" in places else ("onsite" if cities else guess_work_mode(desc)),
            salary_min=lo, salary_max=hi, seniority=guess_seniority(title, desc[:400]),
            experience_years=guess_experience_years(desc), english=guess_english(desc),
            published_at=parse_ua_date(_text(v.select_one(".date"))), apply_type=apply_type, apply_url=apply_url,
            details_loaded=True,
        )


def _split_title(raw: str) -> tuple[str, str | None, str | None, list[str]]:
    """'Middle Dev в Acme, $2200–2800, Київ, віддалено' -> (title, company, salary, places)."""
    raw = raw.replace("&amp;", "&")
    head, sep, tail = raw.rpartition(" в ")
    if not sep:
        return raw, None, None, []
    parts = [p.strip() for p in tail.split(",")]
    salary = next((p for p in parts[1:] if "$" in p), None)
    places = [p for p in parts[1:] if p != salary]
    return head.strip(), parts[0], salary, places


def _text(el) -> str | None:
    return el.get_text(" ", strip=True) if el else None
