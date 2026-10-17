"""Source registry and aggregated search across sources."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from jobs_helper.models import Job, SearchQuery, SourceName
from jobs_helper.sources.base import Source, SourceError
from jobs_helper.sources.djinni import DjinniSource
from jobs_helper.sources.dou import DouSource
from jobs_helper.sources.happymonday import HappyMondaySource

PREFIX_TO_SOURCE: dict[str, SourceName] = {"dou": "dou", "djinni": "djinni", "hm": "happymonday"}

_instances: dict[SourceName, Source] = {}


def get_source(name: SourceName) -> Source:
    if name not in _instances:
        _instances[name] = {"dou": DouSource, "djinni": DjinniSource, "happymonday": HappyMondaySource}[name]()
    return _instances[name]


def source_for_id(job_id: str) -> Source:
    prefix = job_id.split(":", 1)[0] if ":" in job_id else ""
    if prefix not in PREFIX_TO_SOURCE:
        raise SourceError("invalid_job_id", f"'{job_id}' has no known source prefix.",
                          "Job ids look like 'dou:acme/376945', 'djinni:853661-python-dev' or 'hm:2049225'.")
    return get_source(PREFIX_TO_SOURCE[prefix])


def search_all(q: SearchQuery) -> tuple[list[Job], dict[str, str]]:
    """Runs sources in parallel (different hosts, so rate limits don't collide).
    Returns (jobs interleaved by rank, {source: error}) — one failing source does not fail the search."""
    results: dict[str, list[Job]] = {}
    errors: dict[str, str] = {}

    def run(name: SourceName):
        try:
            results[name] = get_source(name).search(q)
        except Exception as e:  # noqa: BLE001 — reported per source
            errors[name] = f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=len(q.sources) or 1) as pool:
        list(pool.map(run, q.sources))
    merged, i = [], 0
    while len(merged) < q.limit and any(i < len(v) for v in results.values()):
        for name in q.sources:
            lst = results.get(name, [])
            if i < len(lst):
                merged.append(lst[i])
        i += 1
    return merged[: q.limit], errors


def get_job(job_id: str) -> Job:
    return source_for_id(job_id).get(job_id)
