"""Browser backend end-to-end on LOCAL html forms (real Chromium, nothing leaves the machine)."""

from pathlib import Path

import pytest

from jobs_helper.apply.backend import ApplyRequest
from jobs_helper.apply.browser import BrowserBackend
from jobs_helper.models import Job

FORMS = Path(__file__).parent / "fixtures" / "forms"
CV = "# Olena Kovalenko\nPython Backend Developer\n\n## Skills\n- Python, Django\n"
COVER = "Dear team, I would love to join you as a backend developer."
PROFILE = {
    "personal": {"first_name": "Olena", "last_name": "Kovalenko", "email": "olena@example.com", "phone": "+380501112233",
                 "city": "Lviv", "country": "Ukraine"},
    "links": {"linkedin": "https://linkedin.com/in/olena-example"},
}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("JOBS_HUMAN_TIMEOUT", "2")
    monkeypatch.setattr("jobs_helper.apply.browser.PROFILES_DIR", tmp_path / "profiles")


def req(form: str, policy="auto", dry_run=False, answers=None, profile=None) -> ApplyRequest:
    job = Job(id="dou:acme/1", source="dou", url="https://example.com", title="Backend Engineer", company="Acme",
              apply_type="external", apply_url=(FORMS / form).as_uri())
    return ApplyRequest(job=job, target="company_site", profile=profile or PROFILE, cv_markdown=CV, cover_letter=COVER,
                        answers=answers or {}, policy=policy, dry_run=dry_run)


def backend():
    return BrowserBackend(headless=True)


def test_needs_input_with_stable_ids():
    out = backend().apply(req("ats_form.html", dry_run=True))
    assert out.status == "needs_input"
    q = {x["field_id"]: x for x in out.questions}
    assert "why_join" in q and not q["why_join"]["human_only"]
    assert "gdpr" in q and q["gdpr"]["human_only"]               # consent: only the human / candidate.yaml
    assert out.filled["First Name *"] == "candidate.yaml" and out.filled["Resume/CV *"] == "cv"


def test_dry_run_fills_and_never_submits():
    prof = PROFILE | {"answers": {"consent_to_processing": True}}
    out = backend().apply(req("ats_form.html", dry_run=True, answers={"why_join": "Your product matches my Django experience."}, profile=prof))
    assert out.status == "prepared" and Path(out.screenshot).exists()
    assert out.filled["How did you hear about us?"] == "job source"     # DOU picked from the job's board


def test_auto_submits_and_detects_confirmation():
    prof = PROFILE | {"answers": {"consent_to_processing": True}}
    out = backend().apply(req("ats_form.html", policy="auto", answers={"why_join": "Your product matches my Django experience."}, profile=prof))
    assert out.status == "submitted", out


def test_captcha_blocks_auto():
    prof = PROFILE | {"answers": {"consent_to_processing": True}}
    out = backend().apply(req("captcha_form.html", policy="auto", answers={"why_join": "x"}, profile=prof))
    assert out.status == "failed" and out.error["error"] == "captcha_required"


def test_confirm_without_answer_sends_nothing():
    prof = PROFILE | {"answers": {"consent_to_processing": True}}
    out = backend().apply(req("ats_form.html", policy="confirm", answers={"why_join": "x"}, profile=prof))
    assert out.status == "declined"


def test_manual_waits_for_human():
    prof = PROFILE | {"answers": {"consent_to_processing": True}}
    out = backend().apply(req("ats_form.html", policy="manual", answers={"why_join": "x"}, profile=prof))
    assert out.status == "awaiting_user"


def test_form_behind_apply_button():
    out = backend().apply(req("apply_button.html", policy="auto"))
    assert out.status == "submitted", out
