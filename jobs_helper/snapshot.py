"""Frozen copies of live vacancies. Evaluations always run on a snapshot, because live data
changes every day; the snapshot store offers the same search/get interface as live sources."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jobs_helper import config
from jobs_helper.models import Job, SearchQuery, now_iso
from jobs_helper.sources import PREFIX_TO_SOURCE, get_job, search_all
from jobs_helper.sources.base import SourceError, rank


def take_snapshot(queries: list[SearchQuery], name: str | None = None, with_details: bool = True,
                  log=print) -> Path:
    jobs: dict[str, Job] = {}
    errors: dict[str, str] = {}
    for q in queries:
        found, errs = search_all(q)
        errors.update({f"{q.keywords}/{k}": v for k, v in errs.items()})
        log(f"  '{q.keywords}': {len(found)} jobs" + (f", errors: {list(errs)}" if errs else ""))
        for j in found:
            jobs.setdefault(j.id, j)
    if with_details:
        for i, (jid, j) in enumerate(list(jobs.items()), 1):
            if not j.details_loaded:
                try:
                    jobs[jid] = get_job(jid)
                except SourceError as e:
                    errors[jid] = e.code
            if i % 10 == 0:
                log(f"  details: {i}/{len(jobs)}")
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SNAPSHOT_DIR / f"{name or datetime.now().strftime('%Y-%m-%d_%H%M')}.json"
    path.write_text(json.dumps({
        "created_at": now_iso(),
        "queries": [q.model_dump() for q in queries],
        "errors": errors,
        "jobs": [j.model_dump() for j in jobs.values()],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def latest_snapshot() -> Path | None:
    files = sorted(config.SNAPSHOT_DIR.glob("*.json"))
    return files[-1] if files else None


class SnapshotStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.created_at = data.get("created_at")
        self.jobs: dict[str, Job] = {j["id"]: Job(**j) for j in data["jobs"]}

    def search(self, q: SearchQuery) -> list[Job]:
        pool = [j for j in self.jobs.values() if j.source in q.sources]
        return rank(pool, q)

    def get(self, job_id: str) -> Job:
        if job_id.split(":", 1)[0] not in PREFIX_TO_SOURCE or ":" not in job_id:
            raise SourceError("invalid_job_id", f"'{job_id}' has no known source prefix.",
                              "Job ids look like 'dou:acme/376945', 'djinni:853661-python-dev' or 'hm:2049225'.")
        if job_id not in self.jobs:
            raise SourceError("job_not_found", f"{job_id} is not in snapshot {self.path.name}.",
                              "Use search_jobs to find vacancies available in this snapshot.")
        return self.jobs[job_id]
