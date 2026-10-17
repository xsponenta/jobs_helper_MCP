"""Tables about the candidate's job search: applications sent through the assistant, vacancies it found or opened,
and activity per week. Data: applications.csv + pipeline.json + workspace/vacancies_log.json."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from jobs_helper import pipeline
from jobs_helper.models import now_iso
from jobs_helper.tracker import Tracker, workspace

LOG_MAX = 3000
STATUS_UA = {"prepared": "підготовлено (тест)", "awaiting_user": "чекає вашого «Надіслати»", "submitted": "надіслано",
             "declined": "скасовано", "failed": "не вдалося", "screening": "відповіли / скринінг", "interview": "співбесіда",
             "test_task": "тестове", "offer": "офер", "accepted": "прийнято", "rejected": "відмова", "withdrawn": "відкликано",
             "ghosted": "без відповіді"}


def _log_path(root: Path | None = None) -> Path:
    return (root or workspace()) / "vacancies_log.json"


def _load_log(root: Path | None = None) -> dict:
    p = _log_path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _save_log(log: dict, root: Path | None = None) -> None:
    if len(log) > LOG_MAX:
        keep = sorted(log.items(), key=lambda kv: kv[1]["last_seen"], reverse=True)[:LOG_MAX]
        log = dict(keep)
    path = _log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")


def record_found(rows: list[dict], query: str, root: Path | None = None) -> None:
    """Search results (short rows from search_jobs)."""
    log, now = _load_log(root), now_iso()
    for r in rows:
        e = log.setdefault(r["job_id"], {"job_id": r["job_id"], "first_seen": now, "times_found": 0, "opened_at": None, "queries": []})
        e.update({k: r.get(k) for k in ("title", "company", "source", "url", "salary", "work_mode")})
        if r.get("fit") is not None:
            e["fit"], e["fit_label"] = r["fit"], r.get("fit_label")
        e["last_seen"], e["times_found"] = now, e["times_found"] + 1
        if query and query not in e["queries"]:
            e["queries"] = (e["queries"] + [query])[-3:]
    _save_log(log, root)


def record_opened(job, fit: dict | None = None, warnings: dict | None = None, root: Path | None = None) -> None:
    """A vacancy the assistant opened in full (get_job / read_vacancy)."""
    log, now = _load_log(root), now_iso()
    e = log.setdefault(job.id, {"job_id": job.id, "first_seen": now, "times_found": 0, "queries": []})
    salary = f"{job.salary_min or ''}-{job.salary_max or ''} USD".strip("-") if (job.salary_min or job.salary_max) else None
    e.update({"title": job.title, "company": job.company, "source": job.source, "url": job.url, "salary": salary,
              "work_mode": job.work_mode, "last_seen": now})
    e["opened_at"] = e.get("opened_at") or now
    if fit and fit.get("score") is not None:
        e["fit"], e["fit_label"] = fit["score"], fit.get("label")
    if warnings:
        e["risk"] = warnings.get("risk")
        e["red_flags"] = [f["flag"] for f in warnings.get("red_flags", [])]
    _save_log(log, root)


def _since(days: int | None) -> str:
    return (pipeline._today() - timedelta(days=days)).isoformat() if days else ""


def applications(days: int | None = None, source: str | None = None, stage: str | None = None,
                 root: Path | None = None) -> list[dict]:
    """Every application attempt through the assistant (and ones logged manually), newest first."""
    root = root or workspace()
    pipe = pipeline.load(root)
    rows = []
    seen = set()
    for t in Tracker(root).rows():
        p = pipe.get(t["application_id"], {})
        seen.add(t["application_id"])
        docs = root / "applications" / t["application_id"]
        rows.append({
            "application_id": t["application_id"], "date": t["created_at"][:10], "company": t["company"], "title": t["title"],
            "source": t["source"], "form": t["target"], "sent_how": t["policy"], "status": p.get("stage") or t["status"],
            "fit": p.get("fit"), "cv_version": p.get("cv_base"), "url": t["url"], "job_id": t["job_id"],
            "next_step": p.get("next_step"), "next_step_date": p.get("next_step_date"),
            "files": p.get("files") or {k: str(root / v) for k, v in (("cv_text", t["cv_path"]), ("cover_letter_text", t["cover_path"])) if v}
                     | ({"screenshot": t["screenshot"]} if t["screenshot"] else {}),
            "folder": str(docs) if docs.exists() else None, "via": "assistant"})
    for k, p in pipe.items():
        if k in seen:
            continue
        rows.append({"application_id": k, "date": p["created_at"][:10], "company": p["company"], "title": p["title"],
                     "source": p["source"], "form": p.get("target") or "manual", "sent_how": "manual", "status": p["stage"],
                     "fit": p.get("fit"), "cv_version": p.get("cv_base"), "url": p.get("url"), "job_id": p.get("job_id"),
                     "next_step": p.get("next_step"), "next_step_date": p.get("next_step_date"), "files": p.get("files") or {},
                     "folder": None, "via": "logged by the candidate"})
    since = _since(days)
    rows = [r for r in rows if r["date"] >= since and (not source or r["source"] == source) and (not stage or r["status"] == stage)]
    for r in rows:
        r["status_ua"] = STATUS_UA.get(r["status"], r["status"])
    return sorted(rows, key=lambda r: (r["date"], r["application_id"]), reverse=True)


def vacancies(days: int | None = None, only_opened: bool = False, min_fit: int | None = None,
              root: Path | None = None) -> list[dict]:
    root = root or workspace()
    applied = {r["job_id"]: r for r in applications(root=root) if r.get("job_id")}
    since = _since(days)
    out = []
    for e in _load_log(root).values():
        if e["last_seen"][:10] < since or (only_opened and not e.get("opened_at")):
            continue
        if min_fit is not None and (e.get("fit") or 0) < min_fit:
            continue
        a = applied.get(e["job_id"])
        out.append({"job_id": e["job_id"], "first_seen": e["first_seen"][:10], "last_seen": e["last_seen"][:10],
                    "title": e.get("title"), "company": e.get("company"), "source": e.get("source"), "salary": e.get("salary"),
                    "work_mode": e.get("work_mode"), "fit": e.get("fit"), "fit_label": e.get("fit_label"), "risk": e.get("risk"),
                    "red_flags": e.get("red_flags", []), "opened": bool(e.get("opened_at")), "times_found": e.get("times_found", 0),
                    "applied": a["application_id"] if a else None, "status": a["status_ua"] if a else None, "url": e.get("url")})
    return sorted(out, key=lambda r: (r["last_seen"], r["fit"] or 0), reverse=True)


def _week(d: str) -> str:
    day = date.fromisoformat(d[:10])
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def weekly(weeks: int = 12, root: Path | None = None) -> list[dict]:
    """Per week (starting Monday): vacancies found / opened, applications, responses, interviews, offers, rejections."""
    root = root or workspace()
    stats: dict[str, dict] = defaultdict(lambda: {"found": 0, "opened": 0, "applied": 0, "responses": 0, "interviews": 0,
                                                  "offers": 0, "rejections": 0, "applied_by_source": defaultdict(int)})
    for e in _load_log(root).values():
        stats[_week(e["first_seen"])]["found"] += 1
        if e.get("opened_at"):
            stats[_week(e["opened_at"])]["opened"] += 1
    for p in pipeline.load(root).values():
        hist = p.get("history", [])
        sent = next((h for h in hist if h["stage"] in ("submitted", "awaiting_user")), None)
        if sent:
            w = stats[_week(sent["at"])]
            w["applied"] += 1
            w["applied_by_source"][p.get("source") or "other"] += 1
        firsts = {}
        for h in hist:
            firsts.setdefault(h["stage"], h["at"])
        resp = min((firsts[s] for s in ("screening", "interview", "test_task", "offer", "rejected") if s in firsts), default=None)
        if resp:
            stats[_week(resp)]["responses"] += 1
        inter = min((firsts[s] for s in ("interview", "test_task") if s in firsts), default=None)
        if inter:
            stats[_week(inter)]["interviews"] += 1
        if "offer" in firsts:
            stats[_week(firsts["offer"])]["offers"] += 1
        if "rejected" in firsts:
            stats[_week(firsts["rejected"])]["rejections"] += 1
    first_monday = date.fromisoformat(_week(pipeline._today().isoformat())) - timedelta(weeks=weeks - 1)
    out = []
    for i in range(weeks):
        wk = (first_monday + timedelta(weeks=i)).isoformat()
        s = stats.get(wk) or stats[wk]
        out.append({"week_of": wk, **{k: v for k, v in s.items() if k != "applied_by_source"},
                    "applied_by_source": dict(s["applied_by_source"])})
    return out
