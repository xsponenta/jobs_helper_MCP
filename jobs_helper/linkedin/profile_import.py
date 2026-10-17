"""Reads the candidate's LinkedIn profile from what LinkedIn itself lets people download:

- "Save to PDF" on the profile page (More → Save to PDF), or
- the data archive (Settings → Data privacy → Get a copy of your data): Profile.csv, Positions.csv, Education.csv, …

No scraping and no API needed. The result is structured facts for create_cv.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

_DATE = r"(?:[A-Za-zА-Яа-яІіЇїЄєҐґ]{3,10}\.?\s+)?(?:19|20)\d{2}"
_RANGE_LINE = re.compile(rf"^(?P<s>{_DATE})\s*[-–—]\s*(?P<e>{_DATE}|Present|Current|Now|дотепер|теперішній час)(?:\s*\((?P<dur>[^)]*)\))?\s*$", re.I)
_DURATION = re.compile(r"^\d+\s+(years?|months?|рок|років|роки|місяц\w*)(\s+\d+\s+(months?|місяц\w*))?$", re.I)
_SIDEBAR = ("contact", "top skills", "languages", "certifications", "honors-awards", "publications", "patents", "контакти",
            "основні навички", "мови", "сертифікати")
_MAIN = ("summary", "experience", "education", "загальні відомості", "досвід", "освіта")


# ---------------------------------------------------------------- data archive (CSV)

def _rows(text: str, expect: str) -> list[dict]:
    """CSV rows; some exported files start with 'Notes:' lines before the header."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if expect.lower() in line.lower()), 0)
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))


def _col(row: dict, *names: str) -> str:
    low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
    return next((low[n.lower()] for n in names if low.get(n.lower())), "")


def _files_from(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_dir():
        for f in path.rglob("*.csv"):
            out[f.name.lower()] = f.read_text(encoding="utf-8-sig", errors="replace")
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.lower().endswith(".csv"):
                    out[Path(n).name.lower()] = z.read(n).decode("utf-8-sig", errors="replace")
    elif path.suffix.lower() == ".csv":
        out[path.name.lower()] = path.read_text(encoding="utf-8-sig", errors="replace")
    return out


def from_archive(path: Path) -> dict:
    files = _files_from(path)
    get = lambda name, expect: _rows(files[name], expect) if name in files else []  # noqa: E731
    prof = (get("profile.csv", "First Name") or [{}])[0]
    emails = get("email addresses.csv", "Email Address")
    primary = next((e for e in emails if _col(e, "Primary").lower() in ("yes", "true")), emails[0] if emails else {})
    phones = get("phonenumbers.csv", "Number") or get("phone numbers.csv", "Number")
    websites = re.findall(r"https?://[^\s,\]]+", _col(prof, "Websites"))
    return {
        "full_name": f"{_col(prof, 'First Name')} {_col(prof, 'Last Name')}".strip(),
        "headline": _col(prof, "Headline"), "summary": _col(prof, "Summary"),
        "location": _col(prof, "Geo Location", "Address"),
        "email": _col(primary, "Email Address") if primary else "", "phone": _col(phones[0], "Number") if phones else "",
        "websites": websites,
        "positions": [{"company": _col(r, "Company Name"), "title": _col(r, "Title"), "location": _col(r, "Location"),
                       "start": _col(r, "Started On"), "end": _col(r, "Finished On") or "present",
                       "description": _col(r, "Description")} for r in get("positions.csv", "Company Name")],
        "education": [{"school": _col(r, "School Name"), "degree": _col(r, "Degree Name"),
                       "year": _col(r, "End Date") or _col(r, "Start Date")} for r in get("education.csv", "School Name")],
        "skills": [_col(r, "Name") for r in get("skills.csv", "Name") if _col(r, "Name")],
        "languages": [f"{_col(r, 'Name')} — {_col(r, 'Proficiency')}".strip(" —") for r in get("languages.csv", "Name")],
        "certifications": [" · ".join(x for x in (_col(r, "Name"), _col(r, "Authority"), _col(r, "Started On")) if x)
                           for r in get("certifications.csv", "Name")],
        "source": "linkedin data archive",
    }


# ---------------------------------------------------------------- "Save to PDF"

def from_pdf(path: Path) -> dict:
    from pypdf import PdfReader
    return parse_profile_text("\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages))


def _is_company_line(line: str) -> bool:
    return len(line) <= 70 and not line.rstrip().endswith((".", ";", ":")) and not _RANGE_LINE.match(line)


def _positions(exp: list[str]) -> list[dict]:
    dates = [i for i, line in enumerate(exp) if _RANGE_LINE.match(line)]
    heads = []                                   # (header start index, company or None, title index)
    for d in dates:
        t = d - 1
        if t < 0:
            continue
        company, start = None, t
        if t - 1 >= 0 and _DURATION.match(exp[t - 1]) and t - 2 >= 0:      # company with several roles
            company, start = exp[t - 2], t - 2
        elif t - 1 >= 0 and _is_company_line(exp[t - 1]) and (not heads or t - 1 > heads[-1][3]):
            company, start = exp[t - 1], t - 1
        heads.append((start, company, t, d))
    out, last_company = [], ""
    for k, (start, company, t, d) in enumerate(heads):
        company = company or last_company
        last_company = company
        j = d + 1
        loc = ""
        if j < len(exp) and len(exp[j]) < 60 and not _RANGE_LINE.match(exp[j]) and \
                ("," in exp[j] or re.search(r"remote|віддален|ukraine|україна|area|region", exp[j], re.I)):
            loc, j = exp[j], j + 1
        stop = heads[k + 1][0] if k + 1 < len(heads) else len(exp)
        desc = [x for x in exp[j:stop] if not _DURATION.match(x)]
        m = _RANGE_LINE.match(exp[d])
        out.append({"company": company, "title": exp[t], "location": loc, "start": m.group("s"), "end": m.group("e"),
                    "description": "\n".join(desc)})
    return out


def parse_profile_text(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not re.match(r"^Page \d+ of \d+$|^Сторінка \d+ з \d+$", line)]
    sections: dict[str, list[str]] = {}
    current, order = "_", []
    for line in lines:
        key = line.lower().rstrip(":")
        if key in _SIDEBAR or key in _MAIN:
            current = key
            order.append(key)
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    # name / headline / location are the 3 lines right before the first main section (Summary or Experience)
    first_main = next((k for k in order if k in _MAIN), None)
    prev = order[order.index(first_main) - 1] if first_main and order.index(first_main) > 0 else "_"
    block = sections.get(prev, [])
    name, headline, location = (block[-3:] if len(block) >= 3 else ["", "", ""])
    if len(block) >= 3:
        sections[prev] = block[:-3]
    contact = sections.get("contact", []) + sections.get("контакти", [])
    email = next((x for x in contact if re.search(r"@[\w-]+\.", x)), "")
    linkedin = next((re.search(r"(?:www\.)?linkedin\.com/in/[\w-]+", x).group(0) for x in contact
                     if re.search(r"linkedin\.com/in/", x)), "")
    phone = next((x for x in contact if re.search(r"\+?\d[\d\s()-]{8,}\d", x)), "")
    edu_lines = sections.get("education", []) + sections.get("освіта", [])
    education = []
    for k, line in enumerate(edu_lines):
        yr = re.search(r"\(([^)]*?)((?:19|20)\d{2})\)\s*$", line)
        if yr and k >= 1:
            education.append({"school": edu_lines[k - 1], "degree": re.sub(r"\s*·?\s*\(.*\)\s*$", "", line), "year": yr.group(2)})
    return {
        "full_name": name, "headline": headline, "location": location, "email": email, "phone": phone,
        "websites": ["https://" + linkedin.removeprefix("www.")] if linkedin else [],
        "summary": " ".join(sections.get("summary", []) + sections.get("загальні відомості", [])),
        "positions": _positions(sections.get("experience", []) + sections.get("досвід", [])), "education": education,
        "skills": sections.get("top skills", []) + sections.get("основні навички", []),
        "languages": sections.get("languages", []) + sections.get("мови", []),
        "certifications": sections.get("certifications", []) + sections.get("сертифікати", []),
        "raw_text": text[:20000], "source": "linkedin profile pdf",
    }


def import_profile(path: Path) -> dict:
    if path.suffix.lower() == ".pdf":
        return from_pdf(path)
    return from_archive(path)


def to_cv_args(p: dict) -> dict:
    """Profile facts -> create_cv arguments (bullets come from the position descriptions, unchanged)."""
    from jobs_helper.cv.builder import Contacts, Education, Position
    linkedin = next((w for w in p.get("websites", []) if "linkedin.com" in w), None)
    positions = []
    for x in p.get("positions", []):
        bullets = [b.strip(" •-–") for b in re.split(r"\n|(?<=[.!?])\s+(?=[A-ZА-ЯІЇЄҐ])", x.get("description") or "") if len(b.strip()) > 3]
        positions.append(Position(role=x["title"] or "—", company=x["company"] or "—", location=x.get("location") or None,
                                  start=x.get("start") or "", end=x.get("end") or "present", bullets=bullets[:6]))
    return {
        "full_name": p.get("full_name") or "", "title": p.get("headline") or (positions[0].role if positions else ""),
        "contacts": Contacts(email=p.get("email") or "email@example.com", phone=p.get("phone") or None,
                             location=p.get("location") or None, linkedin=linkedin),
        "summary": p.get("summary") or "", "experience": positions,
        "skills": {"Skills": p.get("skills", [])} if p.get("skills") else {},
        "education": [Education(degree=e.get("degree") or "", school=e.get("school") or "", year=e.get("year") or None)
                      for e in p.get("education", []) if e.get("school")],
        "languages": p.get("languages") or None, "certifications": p.get("certifications") or None,
    }
