"""Career features offline: fit score, red flags, pipeline, saved searches, company reviews, salary stats,
interview prep and market skill gap."""

from pathlib import Path

import pytest
import yaml

from jobs_helper import config, pipeline
from jobs_helper.cv.review import terms
from jobs_helper.insights import company as comp
from jobs_helper.insights import salary as sal
from jobs_helper.insights.fit import candidate_context, fit
from jobs_helper.insights.interview import prepare
from jobs_helper.insights.market import analyze
from jobs_helper.insights.redflags import remember, vacancy_flags
from jobs_helper.models import Job

CV = """# Olena Kovalenko
Python Backend Developer
olena@example.com · Lviv

## Experience
**Python Developer — Fintech Co** · Mar 2023 – Oct 2026
- Built REST APIs with Django and PostgreSQL serving 40k daily users
- Cut report generation time by 60% with Celery and Redis caching

**Junior Python Developer — Shop Ltd** · Apr 2022 – Feb 2023
- Developed FastAPI services for order processing with Docker

## Skills
Python, Django, FastAPI, PostgreSQL, Redis, Celery, Docker

## Languages
English — Upper-Intermediate (B2)
"""
GOOD = Job(id="djinni:1-py", source="djinni", url="https://djinni.co/jobs/1", title="Middle Python Developer (Django)",
           company="Acme", seniority="middle", experience_years=3, english="B2", salary_min=2500, salary_max=3500,
           published_at="2026-10-14", details_loaded=True,
           description="Responsibilities: build REST APIs with Django and PostgreSQL, use Redis and Docker. "
                       "Interview process: HR call, tech interview. Paid vacation and health insurance. " * 6)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_TODAY", "2026-10-17")
    monkeypatch.setenv("JOBS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("JOBS_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "HOME", tmp_path)
    (tmp_path / "cv.md").write_text(CV, encoding="utf-8")
    cand = tmp_path / "candidate.yaml"
    cand.write_text(yaml.safe_dump({"personal": {"first_name": "Olena", "last_name": "Kovalenko", "email": "o@example.com"},
                                    "languages": {"english": "B2"}, "cv_file": str(tmp_path / "cv.md")}), encoding="utf-8")
    monkeypatch.setenv("JOBS_CANDIDATE", str(cand))
    return tmp_path


def test_terms_ambiguous_names():
    assert terms("Our R&D team will go live soon") == set()
    assert {"r", "go"} <= terms("Experience with R and Go")


def test_fit_strong_and_knockouts(env):
    ctx = candidate_context(CV, {"languages": {"english": "B2"}})
    assert ctx["english"] == "B2" and 4 <= ctx["years"] <= 5 and "sql" in ctx["skills"]   # implied by PostgreSQL
    good = fit(GOOD, ctx)
    assert good["label"] == "strong" and not good["knockouts"] and good["score"] >= 70
    hard = GOOD.model_copy(update={"title": "Senior Go Engineer", "seniority": "lead", "experience_years": 8, "english": "C1",
                                   "description": "Go, Kubernetes, Kafka, gRPC, AWS. 8+ years."})
    bad = fit(hard, ctx)
    assert bad["score"] < 40 and len(bad["knockouts"]) == 3 and "go" in bad["missing"]
    assert fit(GOOD, candidate_context("", {}))["score"] is None


def test_red_flags(env):
    bad = Job(id="djinni:2-x", source="djinni", url="u", title="Junior Python Developer", company="Shady", seniority="junior",
              experience_years=3, published_at="2026-07-01", details_loaded=True,
              description="Шукаємо стресостійкого кандидата, готовність працювати у вихідні. Пиши в t.me/shady_hr. "
                          "Неоплачуване тестове завдання.")
    r = vacancy_flags(bad)
    flags = {f["flag"] for f in r["red_flags"]}
    assert r["risk"] == "high"
    assert {"stale", "level_mismatch", "pressure_language", "informal_channel", "unpaid_work", "no_salary", "vague"} <= flags
    g = vacancy_flags(GOOD, {"found_on_dou": True, "rating": {"score_100": 80, "votes": 40}, "reviews": {"concerns": []}})
    assert g["risk"] == "low" and not g["red_flags"]
    assert any("salary" in x for x in g["green_flags"]) and any("DOU employee rating" in x for x in g["green_flags"])
    low = vacancy_flags(GOOD, {"found_on_dou": True, "rating": {"score_100": 40, "votes": 30}, "reviews": {"concerns": ["workload"]}})
    assert {"low_employee_rating", "reviews:workload"} <= {f["flag"] for f in low["red_flags"]}


def test_repost_detected(env):
    old = GOOD.model_copy(update={"id": "djinni:1-old", "published_at": "2026-08-01"})
    remember([old])
    new = GOOD.model_copy(update={"id": "djinni:9-new", "title": "Senior Python Developer (Django)"})
    remember([new])
    assert "reposted" in {f["flag"] for f in vacancy_flags(new)["red_flags"]}


def test_pipeline_flow(env, monkeypatch):
    pipeline.on_apply("A-0001", {"job_id": GOOD.id, "company": "Acme", "title": GOOD.title, "source": "djinni",
                                 "target": "djinni", "cv_variant": "generated:modern"}, "submitted")
    e = pipeline.update(company="Grammarly", title="Backend Engineer", source="linkedin")
    assert e["application_id"] == "M-0001" and e["stage"] == "submitted"
    pipeline.update("A-0001", stage="screening", contact={"name": "Olena", "role": "Recruiter"},
                    next_step="Tech interview", next_step_date="2026-10-20")
    pipeline.update("A-0001", stage="rejected", note="Went with another candidate")
    with pytest.raises(ValueError):
        pipeline.update("A-0001", stage="nope")
    with pytest.raises(KeyError):
        pipeline.update("A-0404", note="x")
    r = pipeline.report()
    assert r["funnel"]["applied"] == 2 and r["funnel"]["screening_or_further"] == 1 and r["funnel"]["response_rate"] == 0.5
    assert r["by_source"]["djinni"]["response_rate"] == 1.0 and r["by_source"]["linkedin"]["response_rate"] == 0.0
    monkeypatch.setenv("JOBS_TODAY", "2026-10-27")            # 10 days later: follow-up due for the LinkedIn one
    assert [x["application_id"] for x in pipeline.report()["follow_ups_due"]] == ["M-0001"]
    monkeypatch.setenv("JOBS_TODAY", "2026-11-20")            # 5 weeks later: looks ghosted
    assert [x["application_id"] for x in pipeline.report()["probably_ghosted"]] == ["M-0001"]


def test_apply_creates_pipeline_entry(env):
    from jobs_helper.apply.mock import MockBackend
    from jobs_helper.ats import apply_target
    from jobs_helper.mcp_jobs.service import JobsService, SnapshotProvider
    from jobs_helper.tracker import Tracker
    snap = Path(__file__).resolve().parent.parent / "data" / "snapshots" / "2026-10-17_eval.json"
    svc = JobsService(provider=SnapshotProvider(snap), backend=MockBackend(), tracker=Tracker(env / "ws"), role="reviewer")
    job = next(j for j in svc.provider.store.jobs.values() if apply_target(j) == "djinni")
    res = svc.apply(job.id, CV + "\n" + "- more detail line\n" * 20, "Dear team, " + "I build Django APIs with PostgreSQL. " * 8)
    if res.get("error") == "needs_input":                       # answer like the agent would, documents are kept
        res = svc.apply(job.id, answers={q["field_id"]: "3000" for q in res["questions"]})
    assert res.get("ok"), res
    entry = pipeline.load(env / "ws")[res["application_id"]]
    assert entry["job_id"] == job.id and entry["cv_variant"].startswith("generated:") and entry["cv_base"] == "file:cv.md"


def test_search_fit_and_saved_search(env, monkeypatch):
    from jobs_helper import mcp_career
    from jobs_helper.apply.mock import MockBackend
    from jobs_helper.mcp_jobs import server as js
    from jobs_helper.mcp_jobs.service import JobsService, SnapshotProvider
    from jobs_helper.tracker import Tracker
    snap = Path(__file__).resolve().parent.parent / "data" / "snapshots" / "2026-10-17_eval.json"
    svc = JobsService(provider=SnapshotProvider(snap), backend=MockBackend(), tracker=Tracker(env / "ws"), role="reviewer")
    monkeypatch.setattr(js, "_service", svc)
    res = svc.search_jobs("python", limit=10, sort_by="fit")
    fits = [j["fit"] for j in res["jobs"]]
    assert fits == sorted(fits, reverse=True) and all(isinstance(f, int) for f in fits)
    assert len({(j["company"], j["title"]) for j in res["jobs"]}) == len(res["jobs"])        # cross-posts removed
    assert svc.search_jobs("python", sort_by="weird")["error"] == "invalid_argument"
    assert mcp_career.saved_search("save", "py", keywords="python")["ok"]
    first = mcp_career.saved_search("check", "py")["results"][0]
    again = mcp_career.saved_search("check")["results"][0]
    assert first["new_count"] > 0 and again["new_count"] == 0
    assert mcp_career.saved_search("check", "nope")["error"] == "not_found"
    assert mcp_career.saved_search("delete", "py")["ok"] and mcp_career.saved_search("list")["saved"] == []


def test_company_reviews_and_profile(monkeypatch):
    html = """<html><body><div class="company-info"><h1 class="g-h2">Acme</h1> понад 200 спеціалістів
      <div class="offices">Київ, Львів</div><a href="https://acme.example">acme.example</a></div>
      <div class="b-company-about"><div class="b-typo">We build payment software for banks and fintech.</div></div>
      Оцінка компанії: 68/100 Проголосувало 25 співробітників Компенсація 55% Умови праці 70%
      Основний бізнес: Tech Product Є резидентом Diia City</body></html>"""
    reviews_html = "".join(
        f'<div class="b-comment"><div class="b-post-author">Name <span class="prof">QA</span> 3 червня 12:49</div>'
        f'<div class="l-text">{t}</div></div>' for t in (
            "Постійні овертайми і переробки, дедлайни горять. Менеджмент ігнорує проблеми.",
            "Знову переробки на вихідних, вигорання команди. Зарплату не переглядали два роки, це проблема.",
            "Класна команда і дружня атмосфера. Цікавий продукт, рекомендую.",
        ))
    monkeypatch.setattr(comp, "fetch_text", lambda url, ttl=0: reviews_html if url.endswith("/reviews/") else html)
    p = comp.profile("acme")
    assert p["size"] == "понад 200" and p["rating"]["score_100"] == 68 and p["rating"]["votes"] == 25
    assert p["main_business"] == "Tech Product" and p["diia_city"] and p["website"] == "https://acme.example"
    a = comp.analyze_reviews(comp.reviews("acme"))
    assert a["count"] == 3 and "workload" in a["concerns"] and a["themes"]["workload"]["quotes"]
    assert all("Name" not in q for t in a["themes"].values() for q in t["quotes"])
    assert "Fintech / Banking" in comp.spheres(p["about"])
    assert comp.find_dou_slug(job_id="dou:acme/123") == "acme"


def test_salary_stats(monkeypatch):
    rows = [{"income": 1000 + 100 * i, "level": "Middle" if i < 30 else "Senior", "category": "Software Engineering",
             "position": "Software Engineer", "language": "Python", "company_type": "Продуктова", "size": "",
             "experience": 3, "english": "", "spheres": ["Fintech / Banking / Insurance / Capital markets"], "abroad": False}
            for i in range(50)]
    monkeypatch.setattr(sal, "load_rows", lambda path=None: rows)
    monkeypatch.setattr(sal, "latest_file", lambda: Path("2026_june_raw.csv"))
    r = sal.query(language="python", level="middle")
    assert r["overall"]["n"] == 30 and r["overall"]["median"] == round((2400 + 2500) / 2)
    assert sal.query(language="js")["overall"]["n"] == 0
    assert "Fintech / Banking / Insurance / Capital markets" in sal.query(language="python")["by_sphere"]


def test_interview_prep_and_market():
    job = GOOD.model_copy(update={"description": "Requirements:\n- 3+ years of experience with Python and Django\n"
                                                "- Knowledge of Kafka\nResponsibilities: build APIs"})
    p = prepare(job, CV, {"languages": {"english": "B2"}})
    topics = {t["topic"] for t in p["technical_topics"]}
    assert {"python", "django", "kafka"} <= topics
    assert any("kafka" in q["question"].lower() for q in p["risk_questions"])
    assert p["star_material"] and "django" in p["star_material"][0]["matches_vacancy"]
    jobs = [GOOD.model_copy(update={"id": f"x:{i}", "description": "Python, Django, AWS, Kubernetes" + (", Kafka" if i % 2 else "")})
            for i in range(10)]
    m = analyze(jobs, CV)
    gaps = {g["skill"]: g for g in m["gaps"]}
    assert gaps["aws"]["priority"] == "high" and gaps["kubernetes"]["you_have_related"] == ["docker"]
    assert "python" in m["your_strengths"] and m["learning_plan"][0]["skill"] in ("aws", "kubernetes")
    assert analyze([], CV)["vacancies_analyzed"] == 0
