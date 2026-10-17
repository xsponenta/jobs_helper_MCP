"""Application pipeline: what happened after an application was sent — stages, notes, contacts, next steps,
follow-up reminders and a funnel by source / form / CV variant.

Stored in workspace/pipeline.json next to applications.csv. Entries are created by apply (A-xxxx ids) or by the
candidate for applications made elsewhere, e.g. LinkedIn Easy Apply (M-xxxx ids).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from jobs_helper import policy as pol
from jobs_helper.models import now_iso
from jobs_helper.tracker import Tracker, workspace

STAGES = ["prepared", "awaiting_user", "submitted", "screening", "interview", "test_task", "offer", "accepted",
          "rejected", "withdrawn", "ghosted"]
CLOSED = {"accepted", "rejected", "withdrawn", "ghosted"}
# how far an application got: used for the funnel (rejected/withdrawn keep the furthest stage reached)
_ORDER = {"prepared": 0, "awaiting_user": 1, "submitted": 1, "screening": 2, "interview": 3, "test_task": 3, "offer": 4, "accepted": 5}
FOLLOW_UP_DAYS = 7
GHOST_DAYS = 21


def _path(root: Path | None = None) -> Path:
    return (root or workspace()) / "pipeline.json"


def _today() -> date:
    import os
    return date.fromisoformat(os.environ["JOBS_TODAY"]) if os.environ.get("JOBS_TODAY") else date.today()


def load(root: Path | None = None) -> dict[str, dict]:
    path = _path(root)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    # applications sent before the pipeline existed
    for row in Tracker(root).rows():
        if row["application_id"] not in data and row["status"] in ("prepared", "awaiting_user", "submitted"):
            data[row["application_id"]] = _entry(row["application_id"], row, row["status"], row["created_at"])
    return data


def save(data: dict[str, dict], root: Path | None = None) -> None:
    _path(root).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _entry(app_id: str, info: dict, stage: str, at: str | None = None) -> dict:
    at = at or now_iso()
    return {"application_id": app_id, "job_id": info.get("job_id", ""), "company": info.get("company", ""),
            "title": info.get("title", ""), "url": info.get("url", ""), "source": info.get("source", "") or "other",
            "target": info.get("target", ""), "policy": info.get("policy", ""), "cv_variant": info.get("cv_variant", ""),
            "cv_base": info.get("cv_base", ""), "stage": stage, "furthest": stage, "created_at": at, "updated_at": at,
            "history": [{"stage": stage, "at": at}], "notes": [], "contacts": [], "next_step": None, "next_step_date": None}


def cv_base_version() -> str:
    """Short fingerprint of the base CV used at the time (master CV, else the original file name)."""
    if pol.master_cv_path().exists():
        return "master:" + hashlib.sha1(pol.master_cv_path().read_bytes()).hexdigest()[:6]
    f = pol.cv_file(pol.load_candidate())
    return f"file:{f.name}" if f else "none"


def on_apply(app_id: str, info: dict, status: str, root: Path | None = None) -> None:
    """Called by apply after it records an application."""
    data = load(root)
    data[app_id] = _entry(app_id, info | {"cv_base": cv_base_version()}, status)
    save(data, root)


def _furthest(a: str, b: str) -> str:
    return a if _ORDER.get(a, -1) >= _ORDER.get(b, -1) else b


def update(application_id: str | None = None, stage: str | None = None, note: str | None = None,
           contact: dict | None = None, next_step: str | None = None, next_step_date: str | None = None,
           company: str | None = None, title: str | None = None, url: str | None = None, source: str | None = None,
           root: Path | None = None) -> dict:
    data = load(root)
    if not application_id:
        if not (company and title):
            raise ValueError("Pass application_id, or company + title to log an application made elsewhere.")
        nums = [int(k[2:]) for k in data if k.startswith("M-") and k[2:].isdigit()]
        application_id = f"M-{(max(nums) if nums else 0) + 1:04d}"
        data[application_id] = _entry(application_id, {"company": company, "title": title, "url": url or "",
                                                       "source": source or "other", "target": "manual",
                                                       "cv_base": cv_base_version()}, stage or "submitted")
        stage = None
    if application_id not in data:
        raise KeyError(application_id)
    e = data[application_id]
    now = now_iso()
    if stage and stage != e["stage"]:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage '{stage}'. Use one of: {', '.join(STAGES)}.")
        e["history"].append({"stage": stage, "at": now})
        e["stage"], e["furthest"] = stage, _furthest(e.get("furthest", e["stage"]), stage)
        if stage in CLOSED:
            e["next_step"] = e["next_step_date"] = None
    if note:
        e["notes"].append({"at": now, "text": note.strip()})
    if contact and any(contact.values()):
        e["contacts"].append({k: v for k, v in contact.items() if v})
    if next_step is not None:
        e["next_step"] = next_step or None
    if next_step_date is not None:
        date.fromisoformat(next_step_date) if next_step_date else None   # validates
        e["next_step_date"] = next_step_date or None
    e["updated_at"] = now
    save(data, root)
    return e


def _days_since(iso: str) -> int:
    return (_today() - date.fromisoformat(iso[:10])).days


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 2) if d else None


def report(root: Path | None = None) -> dict:
    data = load(root)
    items = list(data.values())
    applied = [e for e in items if _ORDER.get(e.get("furthest", e["stage"]), 0) >= 1 and e["stage"] != "prepared"]
    reached = lambda e, lvl: _ORDER.get(e.get("furthest", e["stage"]), 0) >= lvl  # noqa: E731
    funnel = {"applied": len(applied), "got_response": sum(reached(e, 2) or e["stage"] == "rejected" for e in applied),
              "screening_or_further": sum(reached(e, 2) for e in applied),
              "interview_or_further": sum(reached(e, 3) for e in applied), "offer": sum(reached(e, 4) for e in applied),
              "accepted": sum(e["stage"] == "accepted" for e in applied)}
    funnel["response_rate"] = _rate(funnel["got_response"], funnel["applied"])
    funnel["interview_rate"] = _rate(funnel["interview_or_further"], funnel["applied"])

    def breakdown(field: str) -> dict:
        groups: dict[str, list] = defaultdict(list)
        for e in applied:
            groups[e.get(field) or "unknown"].append(e)
        return {k: {"applied": len(v), "response_rate": _rate(sum(reached(e, 2) or e["stage"] == "rejected" for e in v), len(v)),
                    "interview_rate": _rate(sum(reached(e, 3) for e in v), len(v))} for k, v in groups.items()}

    follow_ups, ghosted, unconfirmed, upcoming = [], [], [], []
    soon = (_today() + timedelta(days=7)).isoformat()
    for e in items:
        if e["stage"] in CLOSED:
            continue
        idle = _days_since(e["updated_at"])
        brief = {"application_id": e["application_id"], "company": e["company"], "title": e["title"], "stage": e["stage"],
                 "days_since_update": idle}
        if e["stage"] == "awaiting_user" and idle >= 1:
            unconfirmed.append(brief | {"ask": "Did you press Submit? Then set stage=submitted."})
        elif e["stage"] in ("submitted", "screening", "test_task") and idle >= GHOST_DAYS:
            ghosted.append(brief | {"suggest": "No news for 3+ weeks: send a last follow-up or mark ghosted."})
        elif e["stage"] in ("submitted", "screening", "interview", "test_task") and idle >= FOLLOW_UP_DAYS:
            follow_ups.append(brief | {"suggest": "Send a short follow-up to the recruiter (offer to draft it)."})
        if e.get("next_step_date") and e["next_step_date"] <= soon:
            upcoming.append({"application_id": e["application_id"], "company": e["company"], "next_step": e["next_step"],
                             "date": e["next_step_date"], "overdue": e["next_step_date"] < _today().isoformat()})
    by_stage = defaultdict(int)
    for e in items:
        by_stage[e["stage"]] += 1
    out = {"total": len(items), "by_stage": dict(by_stage), "funnel": funnel,
           "by_source": breakdown("source"), "by_form": breakdown("target"), "by_cv_variant": breakdown("cv_variant"),
           "by_base_cv": breakdown("cv_base"), "follow_ups_due": follow_ups, "probably_ghosted": ghosted,
           "awaiting_confirmation": unconfirmed, "upcoming": sorted(upcoming, key=lambda x: x["date"]),
           "active": [{k: e[k] for k in ("application_id", "company", "title", "stage", "next_step", "next_step_date", "updated_at")}
                      for e in sorted(items, key=lambda x: x["updated_at"], reverse=True) if e["stage"] not in CLOSED][:25]}
    if len(applied) < 10:
        out["note"] = "Fewer than 10 applications: rates are not meaningful yet."
    return out
