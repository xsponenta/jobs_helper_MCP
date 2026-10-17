"""LinkedIn features: vacancy from a link / text, profile import (PDF text, data archive), profile update with approval."""

import csv
import io
import zipfile
from pathlib import Path

import pytest

F = Path(__file__).parent / "fixtures"
PROFILE_TEXT = """Contact
ann.lee@example.com
www.linkedin.com/in/ann-lee (LinkedIn)
Top Skills
Scrum
Jira
Languages
English (Professional Working)
Ann Lee
Project Manager at PwC | Delivery
Kyiv, Ukraine
Summary
Project manager with 5 years of experience.
Experience
PwC
3 years 2 months
Project Manager
September 2023 - Present (1 year 2 months)
Kyiv, Ukraine
Led a CRM rollout with a $1.2M budget.
Senior Consultant
September 2021 - August 2023 (2 years)
Built a risk register adopted by 6 teams.
SoftServe
Junior Project Manager
September 2019 - August 2021 (2 years)
Lviv, Ukraine
Coordinated 3 Scrum teams.
Education
Kyiv National Economic University
Master's degree, Management · (2017 - 2019)"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("jobs_helper.config.HOME", tmp_path)
    monkeypatch.setenv("JOBS_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("JOBS_CANDIDATE", str(tmp_path / "none.yaml"))
    monkeypatch.setenv("JOBS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("JOBS_DRY_RUN", raising=False)
    return tmp_path


def test_linkedin_vacancy_from_link(home, monkeypatch):
    monkeypatch.setattr("jobs_helper.sources.external.fetch_text", lambda url, ttl=0: (F / "linkedin_job.html").read_text(encoding="utf-8"))
    from jobs_helper.ats import apply_target
    from jobs_helper.sources import get_job
    from jobs_helper.sources.external import read_vacancy
    job = read_vacancy(url="https://ua.linkedin.com/jobs/view/middle-python-developer-at-ajax-systems-4457528360?trk=x")
    assert job.id == "li:4457528360" and job.title == "Middle Python Developer" and job.company == "Ajax Systems"
    assert len(job.description) > 500 and apply_target(job) == "not_a_form:linkedin"
    assert get_job("li:4457528360").company == "Ajax Systems"            # kept locally under a stable id


def test_vacancy_from_text_and_cv_fit(home, monkeypatch):
    (home / "cv.md").write_text((Path(__file__).parent.parent / "data/candidates/olena/cv.md").read_text(encoding="utf-8"), encoding="utf-8")
    from jobs_helper.mcp_all import read_vacancy
    out = read_vacancy(text="Senior Python Engineer\nRequirements: 6+ years of Python, Django, Kafka, Kubernetes, AWS. "
                            "English Upper-Intermediate. Remote. We build a high-load payments platform for Europe.")
    assert out["job_id"].startswith("ext:") and out["cv_fit"]["knockouts"]
    assert "kafka" in out["cv_fit"]["keywords_missing"]


def test_apply_on_linkedin_job_is_handed_to_the_candidate(home, monkeypatch):
    monkeypatch.setattr("jobs_helper.sources.external.fetch_text", lambda url, ttl=0: (F / "linkedin_job.html").read_text(encoding="utf-8"))
    import yaml
    (home / "cand.yaml").write_text(yaml.safe_dump({"personal": {"first_name": "Ann", "last_name": "Lee", "email": "a@example.com"}}))
    monkeypatch.setenv("JOBS_CANDIDATE", str(home / "cand.yaml"))
    from jobs_helper.apply.mock import MockBackend
    from jobs_helper.mcp_jobs.service import JobsService, LiveProvider
    from jobs_helper.sources.external import read_vacancy
    from jobs_helper.tracker import Tracker
    read_vacancy(url="https://www.linkedin.com/jobs/view/4457528360/")
    r = JobsService(provider=LiveProvider(), backend=MockBackend(), tracker=Tracker(home / "ws"), role="reviewer").apply(
        "li:4457528360", "# Ann Lee\n" + "x " * 300, "Dear team, " + "y " * 100)
    assert r["error"] == "external_apply" and "LinkedIn" in r["message"]


def test_profile_pdf_text_with_multi_role_company():
    from jobs_helper.linkedin.profile_import import parse_profile_text
    p = parse_profile_text(PROFILE_TEXT)
    assert (p["full_name"], p["email"]) == ("Ann Lee", "ann.lee@example.com")
    assert [(x["company"], x["title"]) for x in p["positions"]] == [
        ("PwC", "Project Manager"), ("PwC", "Senior Consultant"), ("SoftServe", "Junior Project Manager")]
    assert p["education"][0]["year"] == "2019" and p["skills"] == ["Scrum", "Jira"]


def _archive(path: Path):
    def csv_text(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue()
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Profile.csv", csv_text(["First Name", "Last Name", "Headline", "Summary", "Geo Location", "Websites"],
                                           [["Ann", "Lee", "Project Manager", "PM with 5 years.", "Kyiv, Ukraine",
                                             "[PORTFOLIO:https://ann.dev]"]]))
        z.writestr("Positions.csv", "Notes:\nSome note line\n" + csv_text(
            ["Company Name", "Title", "Description", "Location", "Started On", "Finished On"],
            [["PwC", "Project Manager", "Led a CRM rollout. Cut change requests by 30%.", "Kyiv", "Sep 2021", ""]]))
        z.writestr("Skills.csv", csv_text(["Name"], [["Scrum"], ["Jira"]]))
        z.writestr("Email Addresses.csv", csv_text(["Email Address", "Confirmed", "Primary"], [["ann@example.com", "Yes", "Yes"]]))


def test_archive_import_and_tool_builds_draft(home):
    _archive(home / "Basic_LinkedInDataExport.zip")
    from jobs_helper import policy as pol
    from jobs_helper.mcp_all import import_linkedin_profile
    res, img = import_linkedin_profile(str(home / "Basic_LinkedInDataExport.zip"))
    assert res["profile"]["positions"][0]["end"] == "present" and res["profile"]["email"] == "ann@example.com"
    assert "**Project Manager — PwC, Kyiv** · Sep 2021 – present" in res["cv_markdown"]
    assert "- Cut change requests by 30%." in res["cv_markdown"]
    assert Path(res["path"]).name.startswith("Ann_Lee_Project_Manager") and img.path.exists()
    assert not pol.master_cv_path().exists()                               # a draft, not the default


def test_profile_update_needs_approval(home):
    (home / "cv.md").write_text("# Ann Lee\nPM\n## Experience\n- Led a CRM rollout with a $1.2M budget", encoding="utf-8")
    from jobs_helper.linkedin.service import LinkedInService
    opened = []
    s = LinkedInService(role="reviewer", opener=opened.append)
    draft = s.save_profile_update("Project Manager · Delivery", "I lead delivery. Grew revenue by 45%.", ["Scrum"],
                                  positions=[{"title": "PM", "company": "PwC", "dates": "2021 – present", "description": "Led a CRM rollout."}])
    assert draft["status"] == "draft_saved" and not opened and "45" in draft["unverified"]["numbers"]
    ok = s.save_profile_update("Project Manager · Delivery", "I lead delivery.", ["Scrum"], approved_by_candidate=True)
    assert ok["status"] == "opened_for_candidate" and opened[0].endswith("profile_update.html") and "linkedin.com/in/me" in opened[1]
    assert "Копіювати" in Path(ok["helper_page"]).read_text(encoding="utf-8")
