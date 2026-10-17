"""JobsService on a frozen snapshot with the mock backend: every tool path and error code."""

from pathlib import Path

import pytest
import yaml

from jobs_helper.apply.mock import MockBackend
from jobs_helper.ats import apply_target
from jobs_helper.mcp_jobs.service import JobsService, SnapshotProvider
from jobs_helper.tracker import Tracker

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "snapshots" / "2026-10-17_eval.json"
CV = "# Olena Kovalenko\nPython Backend Developer\n\n## Skills\n- Python, Django, FastAPI, PostgreSQL\n" + "- detail\n" * 60
COVER = "Dear team, I am a Python backend developer with 3.5 years of experience building REST APIs. " * 3

PROFILE = {
    "personal": {"first_name": "Olena", "last_name": "Kovalenko", "email": "olena@example.com",
                 "phone": "+380000000000", "city": "Lviv", "country": "Ukraine"},
    "links": {"linkedin": "https://linkedin.com/in/olena-example"},
    "job_preferences": {"desired_salary_usd": 3500, "notice_period": "2 weeks"},
    "languages": {"english": "B2"},
    "sensitive": {}, "answers": {},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    cand = tmp_path / "candidate.yaml"
    cand.write_text(yaml.safe_dump(PROFILE, allow_unicode=True))
    monkeypatch.setenv("JOBS_CANDIDATE", str(cand))
    monkeypatch.setenv("JOBS_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("JOBS_TODAY", "2026-10-17")
    monkeypatch.delenv("JOBS_DRY_RUN", raising=False)
    return tmp_path


def make(env, role="reviewer") -> JobsService:
    return JobsService(provider=SnapshotProvider(SNAPSHOT), backend=MockBackend(), tracker=Tracker(env / "ws"), role=role)


def first(svc, target_prefix):
    for j in svc.provider.store.jobs.values():
        if apply_target(j).startswith(target_prefix):
            return j
    pytest.skip(f"no job with target {target_prefix} in snapshot")


def test_search_and_get(env):
    svc = make(env)
    res = svc.search_jobs("python", remote=True, limit=5)
    assert res["count"] > 0 and all(j["work_mode"] != "onsite" for j in res["jobs"])
    job = svc.get_job(res["jobs"][0]["job_id"])
    assert job["is_open"] and job["apply_target"]
    assert svc.get_job("dou:nope/1")["error"] == "job_not_found"
    assert svc.get_job("garbage")["error"] == "invalid_job_id"
    assert svc.search_jobs("  ")["error"] == "invalid_argument"


def test_scout_cannot_apply(env):
    job = first(make(env), "djinni")
    assert make(env, role="scout").apply(job.id, CV, COVER)["error"] == "forbidden"


def test_document_bounds(env):
    svc = make(env)
    job = first(svc, "djinni")
    assert svc.apply(job.id, "short", COVER)["error"] == "cv_too_short"
    assert svc.apply(job.id, CV, "hi")["error"] == "cover_letter_too_short"


def test_policies_per_site(env, monkeypatch):
    svc = make(env)
    dj, dou = first(svc, "djinni"), first(svc, "dou")
    r = svc.apply(dj.id, CV, COVER)                       # default djinni: confirm, simulated "yes"
    assert r["status"] == "submitted" and r["policy"] == "confirm"
    r = svc.apply(dou.id, CV, COVER)                      # default dou: manual
    assert r["status"] == "awaiting_user" and r["policy"] == "manual"
    assert svc.apply(dj.id, CV, COVER)["error"] == "already_applied"
    rows = svc.tracker.rows()
    assert [x["status"] for x in rows] == ["submitted", "awaiting_user"]
    assert (svc.tracker.root / rows[0]["cv_path"]).exists()


def test_confirm_declined_and_dry_run(env, monkeypatch):
    svc = make(env)
    jobs = [j for j in svc.provider.store.jobs.values() if apply_target(j) == "djinni"][:2]
    monkeypatch.setenv("JOBS_MOCK_CONFIRM", "no")
    assert svc.apply(jobs[0].id, CV, COVER)["status"] == "declined"
    monkeypatch.setenv("JOBS_DRY_RUN", "1")
    r = svc.apply(jobs[1].id, CV, COVER)
    assert r["status"] == "prepared" and r["policy"] == "dry_run"


def test_needs_input_then_answers(env):
    svc = make(env)
    job = first(svc, "ats:greenhouse")
    r = svc.apply(job.id, CV, COVER)
    assert r["error"] == "needs_input"
    ids = {q["field_id"]: q for q in r["questions"]}
    assert "q_rate" in ids and not ids["q_rate"]["human_only"]      # hourly rate is not the monthly salary
    assert ids["consent"]["human_only"]                              # sensitive: agent cannot answer
    r = svc.apply(job.id, CV, COVER, answers={"q_rate": "25", "consent": "yes"})
    assert r["error"] == "needs_input" and [q["field_id"] for q in r["questions"]] == ["consent"]


def test_sensitive_from_profile(env):
    prof = PROFILE | {"answers": {"consent_to_processing": True}}
    Path(env / "candidate.yaml").write_text(yaml.safe_dump(prof, allow_unicode=True))
    svc = make(env)
    job = first(svc, "ats:greenhouse")
    r = svc.apply(job.id, CV, COVER, answers={"q_rate": "25"})
    assert r.get("ok"), r


def test_closed_job(env, monkeypatch):
    svc = make(env)
    job = next(j for j in svc.provider.store.jobs.values() if j.valid_through)
    monkeypatch.setenv("JOBS_TODAY", "2030-01-01")
    r = svc.apply(job.id, CV, COVER)
    assert r["error"] == "job_closed" and r["retryable"]


def test_not_logged_in(env, monkeypatch):
    monkeypatch.setenv("JOBS_MOCK_SESSIONS", "djinni")
    svc = make(env)
    job = first(svc, "happymonday")
    r = svc.apply(job.id, CV, COVER)
    assert r["error"] == "not_logged_in"


def test_missing_profile(env, monkeypatch):
    monkeypatch.setenv("JOBS_CANDIDATE", str(env / "absent.yaml"))
    svc = make(env)
    assert svc.apply(first(svc, "djinni").id, CV, COVER)["error"] == "candidate_profile_missing"


def test_retry_needs_only_answers(env):
    svc = make(env)
    job = first(svc, "ats:greenhouse")
    assert svc.apply(job.id, CV, COVER)["error"] == "needs_input"
    r = svc.apply(job.id, answers={"q_rate": "25"})                    # no documents on the retry
    assert r["error"] == "needs_input" and [q["field_id"] for q in r["questions"]] == ["consent"]
    assert svc.apply("dou:never-seen/1", answers={"x": "y"})["error"] == "missing_documents"
