"""Offline tests for parsers and heuristics (fixtures saved from real pages on 2026-10-17)."""

from pathlib import Path

import feedparser
import pytest

from jobs_helper import extract
from jobs_helper.models import Job, SearchQuery
from jobs_helper.sources import base
from jobs_helper.sources.djinni import DjinniSource
from jobs_helper.sources.dou import DouSource, _split_title
from jobs_helper.sources.happymonday import HappyMondaySource

F = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- heuristics

@pytest.mark.parametrize("text,expected", [
    ("$2200–2800", (2200, 2800)),
    ("$4000", (4000, 4000)),
    ("від $3000", (3000, None)),
    ("до $4500", (None, 4500)),
    ("no salary", (None, None)),
])
def test_parse_salary(text, expected):
    assert extract.parse_salary(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("5+ years of professional Python\n2+ years with Spark\nAt least 1 year of AWS", 5.0),
    ("Досвід роботи від 3 років з Django", 3.0),
    ("We have been 15 years on the market. 2+ years of experience required", 2.0),
    ("no numbers here", None),
])
def test_guess_experience(text, expected):
    assert extract.guess_experience_years(text) == expected


@pytest.mark.parametrize("title,expected", [
    ("Middle/Senior Python Engineer", "middle"),
    ("Senior React Engineer", "senior"),
    ("Tech Lead (Python / FastAPI)", "lead"),
    ("Python Developer", "unknown"),
])
def test_guess_seniority(title, expected):
    assert extract.guess_seniority(title) == expected


def test_guess_english_and_dates():
    assert extract.guess_english("Requirements: English — Upper-Intermediate") == "B2"
    assert extract.guess_english("Англійська B1 – Середній") == "B1"
    assert extract.parse_ua_date("14 жовтня 2026") == "2026-10-14"


def test_relevance_ignores_generic_words():
    react = extract.relevance("react developer", "React Developer", [], "")
    java = extract.relevance("react developer", "Java Developer", [], "")
    assert react > 0 and java == 0


# ---------------------------------------------------------------- DOU

def test_dou_split_title():
    t, company, salary, places = _split_title("Middle Full Stack Developer в Rayton, $2200–2800, віддалено")
    assert (t, company, salary, places) == ("Middle Full Stack Developer", "Rayton", "$2200–2800", ["віддалено"])


def test_dou_feed_entries():
    feed = feedparser.parse((F / "dou_feed.xml").read_bytes())
    jobs = [DouSource()._from_entry(e) for e in feed.entries]
    assert len(jobs) == 25
    assert all(j.id.startswith("dou:") and "/" in j.id for j in jobs)
    assert all(j.published_at and len(j.published_at) == 10 for j in jobs)
    assert any(j.salary_min for j in jobs)


def test_dou_page_onsite_and_external(monkeypatch):
    monkeypatch.setattr("jobs_helper.sources.dou.resolve_redirects", lambda url: "https://acme.teamtailor.com/jobs/1")
    src = DouSource()
    onsite = src._from_page("dou:quantum/376703", "https://jobs.dou.ua/companies/quantum/vacancies/376703/",
                            (F / "dou_page_onsite.html").read_text())
    assert onsite.apply_type == "on_site" and onsite.company == "Quantum" and onsite.experience_years == 5.0
    ext = src._from_page("dou:glorium-technologies-corp/376697", "https://x/", (F / "dou_page_external.html").read_text())
    assert ext.apply_type == "external" and ext.apply_url == "https://acme.teamtailor.com/jobs/1"


def test_dou_bad_id():
    with pytest.raises(base.SourceError) as e:
        DouSource().get("dou:nonsense")
    assert e.value.code == "invalid_job_id"


# ---------------------------------------------------------------- Djinni

def test_djinni_feed_and_page():
    feed = feedparser.parse((F / "djinni_feed.xml").read_bytes())
    jobs = [DjinniSource()._from_entry(e) for e in feed.entries]
    assert len(jobs) == 100 and all(j.id.startswith("djinni:") for j in jobs)
    job = DjinniSource()._from_page("djinni:853661-middle-python-developer", "https://djinni.co/jobs/853661-middle-python-developer/",
                                    (F / "djinni_page.html").read_text())
    assert job.company and job.details_loaded and job.apply_type == "on_site"
    assert job.experience_years is not None and job.valid_through


# ---------------------------------------------------------------- Happy Monday

def test_hm_page():
    job = HappyMondaySource()._from_page("hm:2049225", "https://happymonday.ua/jobs/2049225", (F / "hm_page.html").read_text())
    assert job.company == "MacPaw"
    assert job.work_mode == "hybrid" and job.cities == ["Київ"]
    assert job.seniority == "junior" and job.english == "B2"
    assert job.published_at == "2026-10-10" and job.valid_through == "2026-11-11"
    assert job.apply_type == "login_required"


# ---------------------------------------------------------------- filters

def _job(**kw) -> Job:
    return Job(**({"id": "dou:a/1", "source": "dou", "url": "u", "title": "Python Developer"} | kw))


def test_filters_keep_unknowns_drop_contradictions():
    q = SearchQuery(keywords="python", remote=True, min_salary=3000, max_experience_years=3)
    assert base.passes_filters(_job(), q)                                   # all unknown -> keep
    assert not base.passes_filters(_job(work_mode="onsite"), q)
    assert not base.passes_filters(_job(salary_min=1500, salary_max=2500), q)
    assert not base.passes_filters(_job(experience_years=5), q)
    assert base.passes_filters(_job(work_mode="remote", salary_max=4000, experience_years=3), q)
