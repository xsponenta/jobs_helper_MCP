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
