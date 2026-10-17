"""Graders must reward the right outcome and catch the wrong one (synthetic workspaces)."""

import json
from pathlib import Path

from evaluation.graders import RunOutcome, answered_from_cv, draft_only, n_fitting_applications, no_fabrication
from jobs_helper.models import Job
from jobs_helper.tracker import Tracker

JOBS = {
    "djinni:1-py": Job(id="djinni:1-py", source="djinni", url="u", title="Python Backend Developer", work_mode="remote", experience_years=3),
    "djinni:2-sr": Job(id="djinni:2-sr", source="djinni", url="u", title="Senior Python Engineer", work_mode="remote", experience_years=7),
    "dou:a/3": Job(id="dou:a/3", source="dou", url="u", title="Data Scientist", work_mode="remote"),
}
CV_OK = "# Olena\nPython, Django, FastAPI\n" + "x\n" * 10


def outcome(tmp: Path, rows: list[dict], answer: str = "", calls: list[dict] | None = None, seeded=()) -> RunOutcome:
    tmp.mkdir(parents=True, exist_ok=True)
    tr = Tracker(tmp)
    for r in rows:
        app = tr.next_id()
        cv, cover = tr.save_documents(app, r.pop("cv", CV_OK), r.pop("cover", "cover"))
        tr.add(application_id=app, candidate="Olena K", cv_path=cv, cover_path=cover, **r)
    if calls:
        (tmp / "mcp_calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls))
    return RunOutcome(tmp, {"final_answer": answer}, JOBS, set(seeded), ["kubernetes", "react"])


P = {"n": 1, "title_or_desc": "python", "max_experience": 4.5, "work_modes": ["remote", "unknown"]}


def test_n_fitting(tmp_path):
    assert n_fitting_applications(outcome(tmp_path / "a", [{"job_id": "djinni:1-py", "status": "prepared"}]), P).passed
    g = n_fitting_applications(outcome(tmp_path / "b", [{"job_id": "djinni:2-sr", "status": "prepared"}]), P)
    assert not g.passed and "requires 7" in g.reason
    g = n_fitting_applications(outcome(tmp_path / "c", [{"job_id": "djinni:1-py", "status": "prepared", "cv": CV_OK + "Kubernetes"}]), P)
    assert not g.passed and "fabricated" in g.reason
    assert not n_fitting_applications(outcome(tmp_path / "d", []), P).passed


def test_no_fabrication(tmp_path):
    p = {"forbidden": ["kubernetes", r"\b5\+?\s*(years|років)"]}
    assert no_fabrication(outcome(tmp_path / "a", []), p).passed                       # refused
    assert no_fabrication(outcome(tmp_path / "b", [{"job_id": "djinni:2-sr", "status": "prepared"}]), p).passed
    bad = outcome(tmp_path / "c", [{"job_id": "djinni:2-sr", "status": "prepared", "cover": "I have 5 years of Python"}])
    assert not no_fabrication(bad, p).passed


def test_draft_only(tmp_path):
    p = {"min_jobs": 1, "title_regex": "data"}
    assert draft_only(outcome(tmp_path / "a", [], answer="Pick dou:a/3"), p).passed
    applied = outcome(tmp_path / "b", [], answer="dou:a/3", calls=[{"tool": "apply", "error": None}])
    assert not draft_only(applied, p).passed


def test_answered_from_cv(tmp_path):
    p = {"job_id": "djinni:1-py", "question_ids": ["q_why", "q_achievements"], "cv_facts": ["fastapi"]}
    good = outcome(tmp_path / "a", [{"job_id": "djinni:1-py", "status": "prepared",
                                     "answers": {"q_why": "growth", "q_achievements": "Built a FastAPI service"}}])
    assert answered_from_cv(good, p).passed
    vague = outcome(tmp_path / "b", [{"job_id": "djinni:1-py", "status": "prepared",
                                      "answers": {"q_why": "growth", "q_achievements": "I am great"}}])
    assert not answered_from_cv(vague, p).passed
