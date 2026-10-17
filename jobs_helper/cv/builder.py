"""Builds a well-structured CV (markdown) from structured facts, so the layout is always right:
standard section order, newest job first, one date format, bullets that start with a capital letter."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_UA_MON = {"січ": 1, "лют": 2, "бер": 3, "кві": 4, "тра": 5, "чер": 6, "лип": 7, "сер": 8, "вер": 9, "жов": 10, "лис": 11, "гру": 12}
_NOW = re.compile(r"^(present|now|current|today|дотепер|теперішній час|зараз|по теперішній час)$", re.I)


class Contacts(BaseModel):
    email: str
    phone: str | None = None
    location: str | None = Field(None, description="City, country; add 'open to remote' if relevant")
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    telegram: str | None = None


class Position(BaseModel):
    role: str
    company: str
    location: str | None = Field(None, description="City or 'remote'")
    start: str = Field(description="Month and year, e.g. 'Mar 2022' or '03/2022'")
    end: str = Field("present", description="Month and year or 'present'")
    bullets: list[str] = Field(description="2-5 achievements: action verb + what + measurable result (only real facts)")


class Education(BaseModel):
    degree: str
    school: str
    year: str | None = None


class Project(BaseModel):
    name: str
    description: str
    link: str | None = None


def norm_date(value: str) -> str:
    v = (value or "").strip()
    if not v or _NOW.match(v):
        return "present"
    m = re.match(r"^(\d{1,2})[./-](\d{4})$", v) or re.match(r"^(\d{4})[./-](\d{1,2})$", v)
    if m:
        a, b = m.groups()
        month, year = (int(a), b) if len(a) <= 2 else (int(b), a)
        return f"{_MON[max(1, min(12, month)) - 1]} {year}"
    m = re.match(r"^([A-Za-zА-Яа-яІіЇїЄєҐґ]{3,})\.?\s+(\d{4})$", v)
    if m:
        word = m.group(1).lower()
        ua = next((n for k, n in _UA_MON.items() if word.startswith(k)), None)
        if ua:
            return f"{_MON[ua - 1]} {m.group(2)}"
        return f"{word[:3].capitalize()} {m.group(2)}"
    return v


def _sort_key(p: Position) -> tuple[int, int]:
    def ym(v: str) -> tuple[int, int]:
        if v == "present":
            return 9999, 12
        m = re.match(r"([A-Za-z]{3}) (\d{4})", v)
        if m and m.group(1) in _MON:
            return int(m.group(2)), _MON.index(m.group(1)) + 1
        y = re.search(r"\d{4}", v)
        return (int(y.group(0)), 1) if y else (0, 0)
    return ym(norm_date(p.end)) + ym(norm_date(p.start))


def _bullet(text: str) -> str:
    t = re.sub(r"^\s*[-•*▪]\s*", "", text).strip().rstrip(";")
    return t[:1].upper() + t[1:] if t else t


def build_markdown(full_name: str, title: str, contacts: Contacts, summary: str, experience: list[Position],
                   skills: dict[str, list[str]] | list[str], education: list[Education] | None = None,
                   languages: list[str] | None = None, certifications: list[str] | None = None,
                   projects: list[Project] | None = None) -> str:
    head_meta = [contacts.location] if contacts.location else []
    contact_line = [c for c in (contacts.email, contacts.phone, contacts.linkedin, contacts.github, contacts.portfolio,
                                contacts.telegram) if c]
    lines = [f"# {full_name.strip()}", " · ".join([title.strip()] + head_meta), " · ".join(contact_line), "",
             "## Summary", summary.strip(), "", "## Experience"]
    for p in sorted(experience, key=_sort_key, reverse=True):
        where = f", {p.location}" if p.location else ""
        lines.append(f"**{p.role.strip()} — {p.company.strip()}{where}** · {norm_date(p.start)} – {norm_date(p.end)}")
        lines += [f"- {_bullet(b)}" for b in p.bullets if b.strip()]
        lines.append("")
    lines.append("## Skills")
    if isinstance(skills, dict):
        lines += [f"- **{group.strip()}:** {', '.join(s.strip() for s in items if s.strip())}" for group, items in skills.items() if items]
    else:
        lines.append(", ".join(s.strip() for s in skills if s.strip()))
    if projects:
        lines += ["", "## Projects"] + [f"- **{p.name.strip()}:** {p.description.strip()}" + (f" ({p.link})" if p.link else "")
                                        for p in projects]
    if education:
        lines += ["", "## Education"] + [f"{e.degree.strip()}, {e.school.strip()}" + (f", {e.year}" if e.year else "") for e in education]
    if certifications:
        lines += ["", "## Certifications"] + [f"- {c.strip()}" for c in certifications if c.strip()]
    if languages:
        lines += ["", "## Languages", " · ".join(language.strip() for language in languages if language.strip())]
    return "\n".join(lines).strip() + "\n"
