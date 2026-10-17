"""Versions of the candidate's main CV. Every time a new CV becomes the default (render_cv / create_cv with
make_default, or a restore) the text is kept as a numbered version with its score, level and design, so the
candidate can see how the CV evolved, compare versions and go back. Version 0 is the original file.

Stored in HOME/cv_history/: vNNN.md (+ vNNN.pdf when one was rendered) and index.json.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from pathlib import Path

from jobs_helper import config
from jobs_helper import policy as pol
from jobs_helper.models import now_iso


def _dir() -> Path:
    d = config.HOME / "cv_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:10]


def load() -> list[dict]:
    path = _dir() / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _save(items: list[dict]) -> None:
    (_dir() / "index.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _quality(text: str) -> tuple[int | None, str | None]:
    try:
        from jobs_helper.cv.review import review
        r = review(text)
        return r["overall"], r["level"]
    except Exception:  # noqa: BLE001 — a score is nice to have
        return None, None


def ensure_baseline() -> list[dict]:
    """Version 0 = the candidate's original CV file (referenced, not copied)."""
    items = load()
    if items:
        return items
    f = pol.cv_file(pol.load_candidate())
    if f:
        try:
            text = pol.read_cv_text(f)
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            score, level = _quality(text)
            items = [{"version": 0, "created_at": now_iso(), "reason": "original file", "source": "original", "sha": sha(text),
                      "file": str(f), "pdf": str(f) if f.suffix.lower() == ".pdf" else None, "template": None,
                      "score": score, "level": level, "words": len(text.split())}]
            _save(items)
    return items


def record(text: str, reason: str, source: str, template: str | None = None, pdf: str | None = None,
           score: int | None = None, level: str | None = None) -> dict:
    """Adds a version unless the text equals the latest one."""
    items = ensure_baseline()
    h = sha(text)
    if items and items[-1]["sha"] == h:
        return items[-1]
    n = (items[-1]["version"] + 1) if items else 1
    md = _dir() / f"v{n:03d}.md"
    md.write_text(text.strip() + "\n", encoding="utf-8")
    pdf_copy = None
    if pdf and Path(pdf).exists():
        pdf_copy = str(shutil.copy(pdf, _dir() / f"v{n:03d}.pdf"))
    if score is None:
        score, level = _quality(text)
    entry = {"version": n, "created_at": now_iso(), "reason": reason, "source": source, "sha": h, "file": str(md),
             "pdf": pdf_copy, "template": template, "score": score, "level": level, "words": len(text.split())}
    items.append(entry)
    _save(items)
    return entry


def text_of(entry: dict) -> str:
    return pol.read_cv_text(Path(entry["file"])) if entry["source"] == "original" else Path(entry["file"]).read_text(encoding="utf-8")


def get(version: int) -> dict | None:
    return next((e for e in ensure_baseline() if e["version"] == version), None)


def current_version() -> int | None:
    """Version number of the CV used now (master CV, else the original file)."""
    items = ensure_baseline()
    if pol.master_cv_path().exists():
        h = sha(pol.master_cv_path().read_text(encoding="utf-8"))
        return next((e["version"] for e in reversed(items) if e["sha"] == h), None)
    return 0 if items else None


def compare(a: int, b: int) -> dict:
    from jobs_helper.cv.review import terms
    ea, eb = get(a), get(b)
    if not ea or not eb:
        raise KeyError(a if not ea else b)
    ta, tb = text_of(ea), text_of(eb)
    la = [x.strip() for x in ta.splitlines() if x.strip()]
    lb = [x.strip() for x in tb.splitlines() if x.strip()]
    diff = [d for d in difflib.unified_diff(la, lb, lineterm="", n=0) if d[:1] in "+-" and d[:3] not in ("+++", "---")]
    return {"from": a, "to": b,
            "score": [ea.get("score"), eb.get("score")], "level": [ea.get("level"), eb.get("level")],
            "words": [ea.get("words"), eb.get("words")],
            "technologies_added": sorted(terms(tb) - terms(ta)), "technologies_removed": sorted(terms(ta) - terms(tb)),
            "lines_added": [d[1:] for d in diff if d[0] == "+"][:25], "lines_removed": [d[1:] for d in diff if d[0] == "-"][:25]}


def restore(version: int) -> dict:
    e = get(version)
    if not e:
        raise KeyError(version)
    if e["source"] == "original":
        pol.master_cv_path().unlink(missing_ok=True)      # back to the original file
        return {"restored": 0, "now_used": e["file"]}
    text = text_of(e)
    pol.master_cv_path().write_text(text.strip() + "\n", encoding="utf-8")
    if e.get("template"):
        pol.set_cv_settings(template=e["template"])
    new = record(text, f"restored v{version}", "restore", e.get("template"), e.get("pdf"), e.get("score"), e.get("level"))
    return {"restored": version, "now_used": str(pol.master_cv_path()), "recorded_as": new["version"]}
