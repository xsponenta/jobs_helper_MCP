"""Documents uploaded to application forms: the tailored CV in the candidate's chosen design + the cover letter."""

from __future__ import annotations

from pathlib import Path

from jobs_helper import policy as pol
from jobs_helper.cv.render import render_cv, render_letter


def render_pdfs(docs: dict[str, str], out_dir: Path) -> dict[str, Path]:
    """{'cv': markdown, 'cover_letter': text} -> {'cv': <Name>_CV.pdf, 'cover_letter': cover_letter.pdf}"""
    out_dir.mkdir(parents=True, exist_ok=True)
    st = pol.cv_settings()
    paths: dict[str, Path] = {}
    if "cv" in docs:
        name = _file_stem(docs["cv"])
        res = render_cv(docs["cv"], out_dir / f"{name}.pdf", theme=st["template"], accent=st["accent"],
                        max_pages=int(st["max_pages"]))
        paths["cv"] = Path(res["path"])
    if "cover_letter" in docs:
        paths["cover_letter"] = Path(render_letter(docs["cover_letter"], out_dir / "cover_letter.pdf"))
    return paths


def _file_stem(cv_markdown: str) -> str:
    """'# Olena Kovalenko' -> 'Olena_Kovalenko_CV' (recruiters see the file name)."""
    import re
    first = next((line[2:].strip() for line in cv_markdown.splitlines() if line.startswith("# ")), "")
    clean = re.sub(r"[^\w\s-]", "", first, flags=re.UNICODE).strip()
    return (re.sub(r"\s+", "_", clean) + "_CV") if clean else "CV"
