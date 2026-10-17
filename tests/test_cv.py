"""CV rendering (design, ATS text layer, page fitting) and CV review scoring."""

from pathlib import Path

import pytest
from pypdf import PdfReader

from jobs_helper.cv.render import cv_html, pdf_preview, render_cv
from jobs_helper.cv.review import review, unverified_claims

ROOT = Path(__file__).resolve().parent.parent
GOOD = (ROOT / "data/candidates/maria/cv.md").read_text(encoding="utf-8")
WEAK = """# Stanislav Vus
Project Manager
stanislav@example.com

## About me
I am a hard-working, stress-resistant team player.

## Experience
**Project Manager — PwC, Kyiv**
- Responsible for project planning and communication with clients
- Worked on reporting for management
- Participated in meetings with stakeholders

## Skills
Jira, Excel
"""


def test_entry_dates_are_aligned_and_lists_parsed():
    html = cv_html(GOOD)
    assert "<div class='entry'>" in html and "Jan 2023 – present" in html
    assert html.count("<li>") >= 10


@pytest.mark.parametrize("theme", ["modern", "classic", "compact"])
def test_render_is_ats_readable(tmp_path, theme):
    r = render_cv(GOOD, tmp_path / "cv.pdf", theme=theme, max_pages=1, preview_png=tmp_path / "p.png")
    text = PdfReader(r["path"]).pages[0].extract_text()
    assert r["pages"] == 1 and "TravelNest" in text and "maria.shevchenko@example.com" in text
    assert (tmp_path / "p.png").stat().st_size > 10_000
    assert pdf_preview(Path(r["path"]), tmp_path / "pp.png") is not None


def test_long_cv_is_fitted(tmp_path):
    fitted = None
    for n in range(8, 30, 3):     # find a length that only fits after shrinking
        md = GOOD + "\n## Projects\n" + "\n".join(f"- Project {i}: built a feature with React, +{i}% engagement" for i in range(n))
        r = render_cv(md, tmp_path / f"cv{n}.pdf", max_pages=1)
        if r["pages"] == 1 and r["scale"] < 1.0:
            fitted = r
            break
    assert fitted, "typography never shrank to fit one page"
    huge = GOOD + "\n## Projects\n" + "\n".join(f"- Project {i}: a long description of a feature built with React" for i in range(60))
    r = render_cv(huge, tmp_path / "cv2.pdf", max_pages=1)
    assert r["pages"] > 1 and "does_not_fit" in r


def test_scores_rank_good_above_weak():
    good, weak = review(GOOD), review(WEAK)
    assert good["overall"] > weak["overall"] + 15
    assert weak["grade"] in ("needs work", "weak")
    msgs = " ".join(i["message"] for i in weak["issues"])
    assert "No dates for: Project Manager — PwC" in msgs and "describe duties" in msgs and "Clichés" in msgs


def test_match_and_unverified():
    job = "Frontend Engineer: React, TypeScript, Next.js, GraphQL, Cypress, Docker"
    r = review(GOOD, job_text=job)
    assert "cypress" in r["details"]["match"]["missing"] and "react" in r["details"]["match"]["covered"]
    u = unverified_claims(GOOD + "\n- Cut bundle size by 37% with Webpack and Docker", GOOD)
    assert "37" in u["numbers"] and {"webpack", "docker"} <= set(u["technologies"])


def test_render_cv_tool_guards_default(tmp_path, monkeypatch):
    monkeypatch.setattr("jobs_helper.config.HOME", tmp_path)
    monkeypatch.setenv("JOBS_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("JOBS_CANDIDATE", str(tmp_path / "none.yaml"))
    (tmp_path / "cv.md").write_text(GOOD, encoding="utf-8")
    from jobs_helper import policy as pol
    from jobs_helper.mcp_all import render_cv as tool
    refused = tool(GOOD + "\n- Led 17 engineers", make_default=True)[0]
    assert refused["error"] == "unverified_facts" and "17" in refused["unverified"]["numbers"]
    ok, image = tool(GOOD, template="compact", make_default=True)
    assert ok["ok"] and Path(ok["path"]).exists() and pol.master_cv_path().exists()
    assert pol.cv_settings()["template"] == "compact" and image.path.exists()


def test_unique_transliterated_file_names(tmp_path):
    from jobs_helper.cv.naming import cv_filename
    assert cv_filename("Олена Коваленко", "Python Developer (Django)", "RetailPulse") == "Olena_Kovalenko_Python_Developer_RetailPulse_CV.pdf"
    (tmp_path / "Ann_Lee_PM_CV.pdf").write_bytes(b"x")
    assert cv_filename("Ann Lee", "PM", directory=tmp_path) == "Ann_Lee_PM_CV_2.pdf"


def test_builder_orders_and_normalises():
    from jobs_helper.cv.builder import Contacts, Position, build_markdown
    md = build_markdown("Ann Lee", "Project Manager", Contacts(email="a@example.com"), "PM with 5 years.",
                        [Position(role="Junior PM", company="A", start="09/2019", end="08/2021", bullets=["did x"]),
                         Position(role="PM", company="B", start="вересень 2021", end="дотепер", bullets=["led y"])],
                        {"Tools": ["Jira"]})
    assert md.index("**PM — B** · Sep 2021 – present") < md.index("**Junior PM — A** · Sep 2019 – Aug 2021")
    assert "- Did x" in md


def test_review_knockouts_timeline_and_ats_name(tmp_path):
    from jobs_helper.cv.review import timeline
    tl = timeline("## Experience\n**PM — B** · Sep 2021 – Aug 2023\n**PM — A** · Jan 2019 – Dec 2020\n## Education\nBSc, 2010 – 2014")
    assert tl["total_years"] == 4.0 and tl["gaps"][0]["months"] == 8          # education dates are not experience
    r = review(WEAK, job_text="Senior Project Manager. 5+ years, Jira, Scrum, Agile, budgeting", job={"title": "Senior Project Manager", "experience_years": 5})
    assert any(k["filter"] == "experience" for k in r["knockouts"]) and r["level"] == "Bronze"
    assert r["action_plan"][0]["gain"] >= r["action_plan"][-1]["gain"]
    bad = review("Contacts\nann@example.com\n+380501112233\nAnn Lee\n## Experience\n- Built things for users daily and more")
    assert any("as your name" in i["message"] for i in bad["issues"])


def test_design_metrics_measure_real_font_size(tmp_path):
    from jobs_helper.cv.review import design_metrics
    r = render_cv(GOOD, tmp_path / "cv.pdf", max_pages=1)
    _, m = design_metrics(Path(r["path"]))
    assert 9.5 <= m["body_font_pt"] <= 11 and m["margins_mm"][0] >= 12


def test_create_cv_tool(tmp_path, monkeypatch):
    monkeypatch.setattr("jobs_helper.config.HOME", tmp_path)
    monkeypatch.setenv("JOBS_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("JOBS_CANDIDATE", str(tmp_path / "none.yaml"))
    from jobs_helper.cv.builder import Contacts, Position
    from jobs_helper.mcp_all import create_cv
    args = dict(full_name="Ann Lee", title="Project Manager", contacts=Contacts(email="ann@example.com", phone="+380501112233"),
                summary="Project manager with 5 years of experience; Scrum, Jira, budgeting; cut delivery time by 20%.",
                experience=[Position(role="Project Manager", company="PwC", start="Sep 2021", end="present",
                                     bullets=["Led a CRM rollout with a $1.2M budget", "Cut change requests by 30%"])],
                skills={"Delivery": ["Scrum", "Jira"]})
    assert create_cv(**args, make_default=True)[0]["error"] == "confirmation_required"
    res, img = create_cv(**args, make_default=True, facts_confirmed_by_candidate=True)
    assert res["path"].endswith("Ann_Lee_Project_Manager_CV.pdf") and res["level"] in ("Gold", "Platinum") and img.path.exists()
