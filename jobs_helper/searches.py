"""Saved searches: the candidate's standing queries, re-run on request to show only vacancies that appeared since
the last check. Stored in workspace/saved_searches.json."""

from __future__ import annotations

import json

from jobs_helper.models import now_iso
from jobs_helper.tracker import workspace

PARAMS = ("keywords", "sources", "remote", "city", "min_salary", "max_experience_years")


def _path():
    return workspace() / "saved_searches.json"


def load() -> dict[str, dict]:
    return json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {}


def _save(data: dict) -> None:
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def save_search(name: str, params: dict) -> dict:
    data = load()
    data[name] = {"params": {k: params.get(k) for k in PARAMS if params.get(k) not in (None, [], "")},
                  "created_at": now_iso(), "last_checked": None, "seen_ids": []}
    _save(data)
    return data[name]


def delete(name: str) -> bool:
    data = load()
    if name not in data:
        return False
    del data[name]
    _save(data)
    return True


def mark_seen(name: str, job_ids: list[str]) -> None:
    data = load()
    e = data[name]
    e["seen_ids"] = list(dict.fromkeys(e["seen_ids"] + job_ids))[-500:]
    e["last_checked"] = now_iso()
    _save(data)
