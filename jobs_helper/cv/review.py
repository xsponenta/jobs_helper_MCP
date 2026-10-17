"""Deterministic CV review: scores, concrete issues and how to fix them.

Categories (0-100 each, overall = weighted mean of the available ones):
  ats           can an applicant-tracking system read the file (text layer, columns, tables, file name, length)
  completeness  contacts, standard sections, dates for every job
  impact        quantified results, action verbs instead of duties, no clichés
  readability   length, bullet size, density
  match         keywords of a vacancy present in the CV (only when a vacancy is given)
The visual side of the design is judged by the model from the page preview this module also produces.
"""

from __future__ import annotations

import re
from pathlib import Path

from jobs_helper.cv.skills import CLICHES, ROLE_WORDS, SECTION_ALIASES, TECH, WEAK_STARTS

PENALTY = {"critical": 40, "major": 18, "minor": 7}
WEIGHTS = {"ats": 0.2, "completeness": 0.25, "impact": 0.3, "readability": 0.15, "match": 0.2}
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_RANGE = re.compile(r"\b(19|20)\d{2}\b.{0,20}?(\b(19|20)\d{2}\b|present|now|current|дотепер|теперішн|зараз)", re.I)
_NUMBER = re.compile(r"\d")
_BULLET = re.compile(r"^\s*([-*•▪●◦]|\d+[.)])\s+")


def _issue(cat, sev, msg, fix, auto=False):
    return {"category": cat, "severity": sev, "message": msg, "fix": fix, "auto_fixable": auto}


# ---------------------------------------------------------------- file / ATS

def file_checks(path: Path) -> tuple[list[dict], dict]:
    issues, info = [], {"file": path.name, "format": path.suffix.lower().lstrip(".")}
    name = path.name
    if re.search(r"\.(pdf|docx?)\.(pdf|docx?)$", name, re.I):
        issues.append(_issue("ats", "minor", f"Double extension in the file name: '{name}'.",
                             "Rename to 'Name_Surname_CV.pdf' — recruiters see the file name.", True))
    elif not re.search(r"cv|resume|резюме", name, re.I):
        issues.append(_issue("ats", "minor", f"File name '{name}' does not say it is a CV.", "Use 'Name_Surname_CV.pdf'.", True))
    if path.stat().st_size > 5 * 1024 * 1024:
        issues.append(_issue("ats", "major", "File is larger than 5 MB; many forms reject it.", "Re-export without heavy images.", True))
    fmt = info["format"]
    if fmt == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        info.update(pages=pages, chars=len(text))
        if len(text.strip()) < 150 * pages:
            issues.append(_issue("ats", "critical", "The PDF has (almost) no text layer — it is a scan or an image.",
                                 "ATS and recruiters' search cannot read it. Re-create the CV as a text PDF (render_cv does this).", True))
        try:
            layout = "\n".join((p.extract_text(extraction_mode="layout") or "") for p in reader.pages)
            lines = [line for line in layout.splitlines() if len(line.strip()) > 20]
            split = [line for line in lines if re.search(r"\S {8,}\S", line.strip())]
            if lines and len(split) / len(lines) > 0.3:
                info["layout"] = "multi-column"
                issues.append(_issue("ats", "major", "Two-column layout: ATS often read columns in the wrong order and mix sections.",
                                     "Use a single-column layout (all render_cv themes are single-column).", True))
        except Exception:  # noqa: BLE001 — layout mode is best-effort
            pass
        images = sum(len(p.images) for p in reader.pages[:2]) if pages else 0
        info["images"] = images
        if images:
            issues.append(_issue("ats", "minor", f"{images} image(s) on the first pages (photo, icons or graphics).",
                                 "Fine for Ukrainian companies; for international ones and ATS prefer a CV without a photo and "
                                 "text instead of icons for contacts.", False))
        if pages > 2:
            issues.append(_issue("readability", "major", f"{pages} pages.", "Keep it to 1-2 pages: cut old / irrelevant experience.", True))
    elif fmt == "docx":
        from docx import Document
        doc = Document(str(path))
        info["tables"] = len(doc.tables)
        if doc.tables:
            issues.append(_issue("ats", "major", "Layout built with tables: ATS may scramble the order.",
                                 "Use plain paragraphs and headings.", True))
        header_text = " ".join(p.text for s in doc.sections for p in s.header.paragraphs)
        if re.search(r"@|\+\d", header_text):
            issues.append(_issue("ats", "major", "Contacts are in the page header; many ATS skip headers.",
                                 "Move email / phone into the body of the first page.", True))
    elif fmt in ("md", "txt"):
        issues.append(_issue("ats", "minor", "Plain text / markdown file: it has no design.",
                             "Render a designed PDF with render_cv before applying.", True))
    return issues, info


# ---------------------------------------------------------------- content

def _sections(text: str) -> set[str]:
    found = set()
    for line in text.splitlines():
        low = line.strip().lower().strip("#*: ")
        if 0 < len(low) <= 40:
            for key, aliases in SECTION_ALIASES.items():
                if any(low == a or low.startswith(a) for a in aliases):
                    found.add(key)
    return found


def _bullets(text: str) -> list[str]:
    return [_BULLET.sub("", line).strip() for line in text.splitlines() if _BULLET.match(line) and len(line.strip()) > 12]


def _job_lines(text: str) -> list[tuple[str, bool]]:
    """(header line, has dates) for lines that look like 'Role — Company' entries."""
    lines = [line.strip() for line in text.splitlines()]
    out = []
    for i, line in enumerate(lines):
        low = line.lower()
        if not (8 < len(line) < 160) or _BULLET.match(line) or line.startswith("##"):
            continue
        looks_role = any(re.search(rf"\b{re.escape(w)}\b", low) for w in ROLE_WORDS)
        has_company_sep = bool(re.search(r"\s[—–-]\s|\s@\s|\bat\b|,\s", line))
        if looks_role and has_company_sep and (line.startswith("**") or line.startswith("###") or _YEAR.search(line)
                                               or (i + 1 < len(lines) and _YEAR.search(lines[i + 1]))):
            window = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            out.append((line.strip("*# "), bool(_RANGE.search(window) or _YEAR.search(window))))
    return out


def content_checks(text: str) -> tuple[list[dict], dict]:
    issues, low = [], text.lower()
    words = len(re.findall(r"\w+", text))
    info = {"words": words}
    # completeness
    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        issues.append(_issue("completeness", "critical", "No email.", "Add an email at the top."))
    if not re.search(r"\+?\d[\d\s()-]{8,}\d", text):
        issues.append(_issue("completeness", "major", "No phone number.", "Add a phone in +380… format."))
    if "linkedin.com" not in low:
        issues.append(_issue("completeness", "minor", "No LinkedIn link.", "Add linkedin.com/in/… next to the contacts."))
    secs = _sections(text)
    info["sections"] = sorted(secs)
    for key, sev in (("experience", "critical"), ("skills", "major"), ("summary", "minor"), ("education", "minor"),
                     ("languages", "minor")):
        if key not in secs:
            issues.append(_issue("completeness", sev, f"No '{key}' section.", f"Add a clearly titled '{key.capitalize()}' section."))
    jobs = _job_lines(text)
    info["jobs_detected"] = len(jobs)
    undated = [j for j, dated in jobs if not dated]
    if undated:
        issues.append(_issue("completeness", "major", f"No dates for: {'; '.join(u[:60] for u in undated[:4])}.",
                             "Add month/year – month/year for every position; recruiters skip CVs with unclear timelines."))
    # impact
    bullets = _bullets(text)
    info["bullets"] = len(bullets)
    if bullets:
        quantified = [b for b in bullets if _NUMBER.search(b)]
        share = len(quantified) / len(bullets)
        info["quantified_share"] = round(share, 2)
        if share < 0.3:
            issues.append(_issue("impact", "major", f"Only {len(quantified)} of {len(bullets)} bullets have numbers.",
                                 "Add measurable results where you know them: %, time saved, users, revenue, team size. "
                                 "Never invent numbers — ask the candidate."))
        weak = [b for b in bullets if b.lower().startswith(WEAK_STARTS)]
        if weak:
            issues.append(_issue("impact", "major" if len(weak) > 2 else "minor",
                                 f"{len(weak)} bullet(s) describe duties, not results: “{weak[0][:70]}…”.",
                                 "Start with an action verb and the outcome: 'Built…', 'Reduced…', 'Launched…'."))
        long_b = [b for b in bullets if len(b.split()) > 32]
        if long_b:
            issues.append(_issue("readability", "minor", f"{len(long_b)} bullet(s) longer than 32 words.", "Split or shorten to 1-2 lines."))
    elif "experience" in secs:
        issues.append(_issue("impact", "major", "Experience is written as paragraphs, without bullet points.",
                             "Use 2-5 bullets per position, each with an action and a result."))
    cliches = sorted({c for c in CLICHES if c in low})
    if cliches:
        issues.append(_issue("impact", "minor", f"Clichés: {', '.join(cliches[:5])}.", "Replace with evidence (what you did and the result)."))
    if re.search(r"(^|\n)\s*[-•*]?\s*(i|my|я|мій|моя)\b", low):
        issues.append(_issue("readability", "minor", "First-person sentences in bullets.", "Drop 'I/my': start with the verb."))
    # readability
    if words < 150:
        issues.append(_issue("readability", "major", f"Very short CV ({words} words).", "Describe achievements for each role."))
    elif words > 950:
        issues.append(_issue("readability", "major", f"Long CV ({words} words).", "Aim for 350-700 words: keep what matters for the target role.", True))
    return issues, info


# ---------------------------------------------------------------- vacancy match / unverified claims

def terms(text: str) -> set[str]:
    low = text.lower()
    return {t for t in TECH if re.search(rf"(?<![\w.+#-]){re.escape(t)}(?![\w+#-])", low)}


def match_checks(cv_text: str, job_text: str) -> tuple[list[dict], dict]:
    wanted, have = terms(job_text), terms(cv_text)
    missing = sorted(wanted - have)
    covered = sorted(wanted & have)
    info = {"vacancy_keywords": sorted(wanted), "covered": covered, "missing": missing,
            "coverage": round(len(covered) / len(wanted), 2) if wanted else None}
    issues = []
    if wanted and len(covered) / len(wanted) < 0.6:
        issues.append(_issue("match", "major", f"The CV mentions {len(covered)} of {len(wanted)} key technologies of the vacancy.",
                             f"If the candidate really has them, make them visible (skills + a bullet): {', '.join(missing[:8])}. "
                             "If not — do NOT add them; mention learning plans in the cover letter."))
    elif missing:
        issues.append(_issue("match", "minor", f"Not mentioned: {', '.join(missing[:8])}.",
                             "Add only those the candidate actually used."))
    return issues, info


def unverified_claims(new_text: str, sources: str) -> dict:
    """Numbers and technologies in a rewritten CV that appear nowhere in the candidate's own documents / notes."""
    src_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", sources))
    nums = sorted({n for n in re.findall(r"\d+(?:[.,]\d+)?", new_text) if n not in src_numbers and not _YEAR.fullmatch(n)})
    return {"numbers": nums, "technologies": sorted(terms(new_text) - terms(sources))}


# ---------------------------------------------------------------- report

def review(text: str, file: Path | None = None, job_text: str | None = None) -> dict:
    issues, info = [], {}
    if file:
        fi, finfo = file_checks(file)
        issues += fi
        info["file"] = finfo
        if any(i["severity"] == "critical" and "text layer" in i["message"] for i in fi):
            # nothing readable: content checks would only repeat "no email / no experience"
            return {"overall": min(35, max(0, 100 - sum(PENALTY[i["severity"]] for i in fi))), "grade": "weak",
                    "scores": {"ats": max(0, 100 - sum(PENALTY[i["severity"]] for i in fi))}, "issues": fi,
                    "top_fixes": [i["fix"] for i in fi[:5]], "details": info, "design_fixable": True,
                    "note": "Content was not checked: the file has no text. Ask the candidate for a text version or rebuild it."}
    ci, cinfo = content_checks(text)
    issues += ci
    info["content"] = cinfo
    if job_text:
        mi, minfo = match_checks(text, job_text)
        issues += mi
        info["match"] = minfo
    cats = ["ats", "completeness", "impact", "readability"] + (["match"] if job_text else [])
    scores = {c: max(0, 100 - sum(PENALTY[i["severity"]] for i in issues if i["category"] == c)) for c in cats}
    total_w = sum(WEIGHTS[c] for c in cats)
    overall = round(sum(scores[c] * WEIGHTS[c] for c in cats) / total_w)
    n_major = sum(i["severity"] == "major" for i in issues)
    if any(i["severity"] == "critical" for i in issues):
        overall = min(overall, 55)
    elif n_major >= 4:
        overall = min(overall, 65)
    elif n_major >= 3:
        overall = min(overall, 72)
    elif n_major >= 2:
        overall = min(overall, 79)
    order = {"critical": 0, "major": 1, "minor": 2}
    issues.sort(key=lambda i: (order[i["severity"]], -WEIGHTS[i["category"]]))
    grade = "strong" if overall >= 88 else "good" if overall >= 75 else "needs work" if overall >= 55 else "weak"
    return {"overall": overall, "grade": grade, "scores": scores, "issues": issues,
            "top_fixes": [i["fix"] for i in issues[:5]], "details": info,
            "design_fixable": any(i["auto_fixable"] and i["category"] in ("ats", "readability") for i in issues)}
