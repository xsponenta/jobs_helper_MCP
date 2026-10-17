"""Happy Monday: no API / RSS. job-sitemap.xml lists every vacancy with lastmod; details are parsed
from HTML and kept in a local index, refreshed incrementally (newest / changed pages first)."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from jobs_helper import config
from jobs_helper.extract import guess_experience_years, guess_seniority, html_to_text, parse_ua_date, seniority_from_text
from jobs_helper.http import FetchError, fetch_text
from jobs_helper.models import Job, SearchQuery
from jobs_helper.sources.base import Source, SourceError, rank

SITEMAP = "https://happymonday.ua/job-sitemap.xml"
URL_RE = re.compile(r"happymonday\.ua/jobs/(\d+)")
ENGLISH_CEFR = re.compile(r"\((A1|A2|B1|B2|C1|C2)\)")


class HappyMondaySource(Source):
    name = "happymonday"

    def __init__(self, sync_budget: int = 30):
        self.sync_budget = sync_budget   # max pages fetched per search call
        self.last_sync: dict = {}

    # ------------------------------------------------------------ index
    def _load_index(self) -> dict:
        try:
            return json.loads(config.HM_INDEX_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_index(self, idx: dict) -> None:
        config.HM_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.HM_INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False))

    def sitemap(self) -> list[tuple[str, str]]:
        xml = fetch_text(SITEMAP, ttl=3600)
        urls = re.findall(r"<loc>([^<]+)</loc>", xml)
        mods = re.findall(r"<lastmod>([^<]+)</lastmod>", xml)
        return list(zip(urls, mods + [""] * (len(urls) - len(mods))))

    def sync(self, budget: int | None = None) -> dict:
        """Fetch pages that are new or changed since the last sync, newest first."""
        budget = self.sync_budget if budget is None else budget
        idx = self._load_index()
        entries = sorted(self.sitemap(), key=lambda x: x[1], reverse=True)
        live = {u for u, _ in entries}
        removed = [u for u in idx if u not in live]
        for u in removed:
            idx.pop(u)
        todo = [(u, m) for u, m in entries if idx.get(u, {}).get("lastmod") != m]
        fetched = errors = 0
        for url, mod in todo[:budget]:
            vid = URL_RE.search(url).group(1)
            try:
                job = self._from_page(f"hm:{vid}", url, fetch_text(url))
                idx[url] = {"lastmod": mod, "job": job.model_dump()}
                fetched += 1
            except (FetchError, SourceError):
                errors += 1
        self._save_index(idx)
        self.last_sync = {"in_sitemap": len(entries), "indexed": len(idx), "fetched": fetched,
                          "pending": max(0, len(todo) - budget), "removed": len(removed), "errors": errors}
        return self.last_sync

    def indexed_jobs(self) -> list[Job]:
        return [Job(**v["job"]) for v in self._load_index().values()]

    # ------------------------------------------------------------ search / details
    def search(self, q: SearchQuery) -> list[Job]:
        try:
            self.sync()
        except FetchError:
            pass  # search over whatever is already indexed
        return rank(self.indexed_jobs(), q)

    def get(self, job_id: str) -> Job:
        key = self.key(job_id)
        if not key.isdigit():
            raise SourceError("invalid_job_id", f"'{job_id}' is malformed.", "Happy Monday ids look like 'hm:<number>'.")
        url = f"https://happymonday.ua/jobs/{key}"
        try:
            html = fetch_text(url)
        except FetchError as e:
            if e.status == 404:
                raise SourceError("job_not_found", f"{job_id} does not exist or was removed.", "Use search_jobs to find current vacancies.")
            raise SourceError("source_unavailable", f"Happy Monday is not reachable: {e}", "Retry later.")
        return self._from_page(job_id, url, html)

    def _from_page(self, job_id: str, url: str, html: str) -> Job:
        s = BeautifulSoup(html, "lxml")
        if not s.select_one(".single-vacancy"):
            raise SourceError("job_not_found", f"{job_id} has no vacancy content.", "It was probably removed.")
        plate = lambda cls: _text(s.select_one(f".job_plate.{cls}"))  # noqa: E731
        remote_txt = (plate("_jobremote") or "").lower()
        work_mode = ("hybrid" if "гібрид" in remote_txt else "remote" if "віддал" in remote_txt
                     else "onsite" if "офіс" in remote_txt else "unknown")
        city = plate("_joblocation")
        english_raw = _text(s.select_one(".job_plate._jobenglish .hm_tooltip_label"))
        if english_raw:
            english_raw = english_raw.replace("Рівень англійської", "").strip()
        m = ENGLISH_CEFR.search(english_raw or "")
        english = m.group(1) if m else (None if not english_raw or "необов" in english_raw else english_raw)
        dates = _text(s.select_one(".single-vacancy__date")) or ""
        title = _text(s.select_one(".single-vacancy__title h1")) or _text(s.h1) or ""
        desc = html_to_text(str(s.select_one(".single-vacancy__text") or ""))
        level = seniority_from_text(plate("_joblevel"))
        partner = "З партнерських джерел" in s.get_text(" ", strip=True)
        return Job(
            id=job_id, source="happymonday", url=url, title=title,
            company=_text(s.select_one(".single-vacancy__meta-place")), description=desc,
            location=", ".join(x for x in [plate("_jobremote"), city] if x) or None,
            cities=[city] if city else [], work_mode=work_mode,
            seniority=level if level != "unknown" else guess_seniority(title),
            experience_years=guess_experience_years(desc), english=english,
            tags=[t for t in [plate("_jobtype"), "partner_source" if partner else None] if t],
            published_at=_ua_date(dates, "Опублікована"), valid_through=_ua_date(dates, "Активна до"),
            apply_type="login_required", apply_url=url, details_loaded=True,
        )


def _ua_date(text: str, label: str) -> str | None:
    m = re.search(label + r":\s*(\d{1,2}\s+\w+\s+\d{4})", text)
    return parse_ua_date(m.group(1)) if m else None


def _text(el) -> str | None:
    t = el.get_text(" ", strip=True) if el else None
    return t or None
