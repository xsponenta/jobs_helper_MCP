"""Common interface for job sources plus the local filtering / ranking used by all of them."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from jobs_helper.extract import relevance
from jobs_helper.models import Job, SearchQuery, SourceName

_SENIORITY_ORDER = ["intern", "junior", "middle", "senior", "lead"]


class SourceError(Exception):
    """Raised by sources; `code` is mapped to a tool error by the MCP layer."""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


class Source(ABC):
    name: SourceName

    @abstractmethod
    def search(self, q: SearchQuery) -> list[Job]:
        """Jobs matching the query, best first, at most q.limit."""

    @abstractmethod
    def get(self, job_id: str) -> Job:
        """Full vacancy (details_loaded=True). Raises SourceError('job_not_found', ...)."""

    def key(self, job_id: str) -> str:
        prefix = {"dou": "dou:", "djinni": "djinni:", "happymonday": "hm:"}[self.name]
        if not job_id.startswith(prefix):
            raise SourceError("invalid_job_id", f"'{job_id}' is not a {self.name} id.", f"Ids of this source start with '{prefix}'.")
        return job_id[len(prefix):]


def passes_filters(job: Job, q: SearchQuery) -> bool:
    """Unknown values never exclude a job: we only drop what is known to contradict the query."""
    if q.remote is True and job.work_mode in ("onsite", "hybrid"):
        return False
    if q.city and job.cities and not any(q.city.lower() in c.lower() for c in job.cities):
        if job.work_mode != "remote":
            return False
    if q.min_salary is not None:
        top = job.salary_max or job.salary_min
        if top is not None and top < q.min_salary:
            return False
    if q.max_experience_years is not None and job.experience_years is not None:
        if job.experience_years > q.max_experience_years + 0.5:
            return False
    if q.seniority and job.seniority != "unknown" and q.seniority in _SENIORITY_ORDER and job.seniority in _SENIORITY_ORDER:
        if abs(_SENIORITY_ORDER.index(job.seniority) - _SENIORITY_ORDER.index(q.seniority)) > 1:
            return False
    return True


def rank(jobs: list[Job], q: SearchQuery, require_match: bool = True) -> list[Job]:
    scored = []
    for j in jobs:
        if not passes_filters(j, q):
            continue
        score = relevance(q.keywords, j.title, j.tags, j.description)
        if require_match and score == 0:
            continue
        scored.append((score, j.published_at or "", j))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)  # higher score first, then newer
    return [j for _, _, j in scored[: q.limit]]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9а-яіїєґ+#]", "", s.lower())
