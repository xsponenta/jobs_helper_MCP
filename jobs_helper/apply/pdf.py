"""Documents uploaded to application forms: the tailored CV in the candidate's chosen design + the cover letter."""

from __future__ import annotations

from pathlib import Path

from jobs_helper import policy as pol
from jobs_helper.cv.naming import cv_filename, name_from_markdown
from jobs_helper.cv.render import render_cv, render_letter


def render_pdfs(docs: dict[str, str], out_dir: Path, role: str | None = None, company: str | None = None) -> dict[str, Path]:
    """{'cv': markdown, 'cover_letter': text} -> unique, recruiter-friendly file names, e.g.
    Olena_Kovalenko_Python_Developer_RetailPulse_CV.pdf and ..._Cover_Letter.pdf"""
    out_dir.mkdir(parents=True, exist_ok=True)
    st = pol.cv_settings()
    paths: dict[str, Path] = {}
    name = name_from_markdown(docs.get("cv", ""))
    if "cv" in docs:
        fname = cv_filename(name, role, company, directory=out_dir)
        res = render_cv(docs["cv"], out_dir / fname, theme=st["template"], accent=st["accent"], max_pages=int(st["max_pages"]))
        paths["cv"] = Path(res["path"])
    if "cover_letter" in docs:
        fname = cv_filename(name, role, company, directory=out_dir).replace("_CV.pdf", "_Cover_Letter.pdf")
        paths["cover_letter"] = Path(render_letter(docs["cover_letter"], out_dir / fname))
    return paths


def _file_stem(cv_markdown: str) -> str:   # kept for callers that only need a base name
    return cv_filename(name_from_markdown(cv_markdown))[:-4]
