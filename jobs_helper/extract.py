"""Heuristics that pull structured fields out of free-form vacancy text (UA / EN)."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

from jobs_helper.models import Seniority, WorkMode


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "lxml")
    for br in soup.find_all(["br", "p", "li", "h1", "h2", "h3", "h4", "div"]):
        br.append("\n")
    text = html.unescape(soup.get_text())
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


# ---------------------------------------------------------------- salary

_SALARY_RE = re.compile(r"(від|до|from|up to)?\s*\$\s?(\d[\d\s]*)(?:\s*[–—-]\s*\$?\s?(\d[\d\s]*))?", re.I)


def parse_salary(text: str | None) -> tuple[int | None, int | None]:
    """'$2200–2800' -> (2200, 2800); 'від $3000' -> (3000, None); 'до $4000' -> (None, 4000)."""
    if not text:
        return None, None
    m = _SALARY_RE.search(text)
    if not m:
        return None, None
    prefix = (m.group(1) or "").lower()
    lo = int(re.sub(r"\D", "", m.group(2)))
    hi = int(re.sub(r"\D", "", m.group(3))) if m.group(3) else None
    if prefix in ("до", "up to"):
        return None, lo
    if prefix in ("від", "from"):
        return lo, hi
    return lo, hi if hi is not None else lo


# ---------------------------------------------------------------- seniority

_SENIORITY_PATTERNS: list[tuple[Seniority, str]] = [
    ("lead", r"\b(lead|head of|principal|staff|architect|тімлід|лід)\b"),
    ("senior", r"\b(senior|sr\.?)\b"),
    ("middle", r"\b(middle|mid[- ]level|mid)\b"),
    ("junior", r"\b(junior|jr\.?|джуніор)\b"),
    ("intern", r"\b(intern|trainee|internship|стажер|стажування)\b"),
]


def guess_seniority(title: str, extra: str = "") -> Seniority:
    """Title wins; 'Middle/Senior' resolves to the lower level (more inclusive for candidates)."""
    for text in (title, extra):
        found = [lvl for lvl, pat in _SENIORITY_PATTERNS if re.search(pat, text or "", re.I)]
        if found:
            order = ["intern", "junior", "middle", "senior", "lead"]
            return min(found, key=order.index)
    return "unknown"


def seniority_from_text(word: str | None) -> Seniority:
    if not word:
        return "unknown"
    w = word.strip().lower()
    for lvl in ("intern", "junior", "middle", "senior", "lead"):
        if lvl in w:
            return lvl  # type: ignore[return-value]
    return "unknown"


# ---------------------------------------------------------------- experience

_EXP_RE = re.compile(
    r"(?:(?:at least|minimum|min\.?|від|не менше|понад|більше)\s*)?"
    r"(\d{1,2}(?:[.,]5)?)\s*\+?\s*(?:[-–]\s*\d{1,2}\s*)?"
    r"(?:\+\s*)?(years?|yrs?|роки|років|рік|року)",
    re.I,
)


_NOT_EXPERIENCE = re.compile(r"on the market|на ринку|of history|founded|existence|років тому|\bago\b", re.I)


def guess_experience_years(text: str) -> float | None:
    """Main requirement = the largest plausible 'N+ years' mention ('5+ years Python, 2+ years Spark' -> 5).
    Mentions about the company itself ('10 years on the market') are skipped."""
    vals = []
    for m in _EXP_RE.finditer(text or ""):
        if _NOT_EXPERIENCE.search(text[m.end(): m.end() + 40]):
            continue
        v = float(m.group(1).replace(",", "."))
        if 0 < v <= 12:
            vals.append(v)
    return max(vals) if vals else None


# ---------------------------------------------------------------- english

_ENGLISH_RE = re.compile(
    r"\b(A1|A2|B1|B2|C1|C2|pre-?intermediate|upper[- ]intermediate|intermediate|advanced|fluent|native)\b", re.I
)
_ENGLISH_NORM = {"pre-intermediate": "A2", "preintermediate": "A2", "intermediate": "B1",
                 "upper-intermediate": "B2", "upper intermediate": "B2", "advanced": "C1", "fluent": "C1", "native": "C2"}


def guess_english(text: str) -> str | None:
    """Looks for a level near the word English/англійська."""
    for m in re.finditer(r"(english|англійськ\w*)", text or "", re.I):
        window = text[max(0, m.start() - 60): m.end() + 60]
        lvl = _ENGLISH_RE.search(window)
        if lvl:
            v = lvl.group(1).lower()
            return _ENGLISH_NORM.get(v, v.upper())
    return None


# ---------------------------------------------------------------- work mode

def guess_work_mode(text: str) -> WorkMode:
    t = (text or "").lower()
    if re.search(r"гібрид|hybrid", t):
        return "hybrid"
    if re.search(r"віддален|remote|telecommute|дистанц", t):
        return "remote"
    if re.search(r"в офісі|офіс|onsite|on-site|office", t):
        return "onsite"
    return "unknown"


# ---------------------------------------------------------------- relevance

# generic words that say nothing about the role's stack
STOPWORDS = {
    "developer", "engineer", "розробник", "розробниця", "інженер", "programmer", "software", "specialist",
    "senior", "middle", "junior", "lead", "strong", "remote", "віддалено", "job", "вакансія", "робота",
    "and", "or", "the", "for", "with", "in", "of", "та", "і", "в", "на", "з",
}


def tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w+#.]+", (text or "").lower()) if len(t) > 1]


def relevance(keywords: str, title: str, tags: list[str], description: str) -> float:
    """Simple weighted keyword overlap: title x3, tags x2, description x1."""
    kws = set(tokenize(keywords)) - STOPWORDS or set(tokenize(keywords))
    if not kws:
        return 0.0
    title_t, tag_t, desc_t = set(tokenize(title)), set(tokenize(" ".join(tags))), set(tokenize(description))
    return 3 * len(kws & title_t) + 2 * len(kws & tag_t) + len(kws & desc_t)


# ---------------------------------------------------------------- dates

_UA_MONTHS = {"січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6, "липня": 7,
              "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12}


def parse_ua_date(text: str | None) -> str | None:
    """'14 жовтня 2026' -> '2026-10-14'."""
    m = re.search(r"(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})", (text or "").lower())
    if not m or m.group(2) not in _UA_MONTHS:
        return None
    return f"{int(m.group(3)):04d}-{_UA_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"


def feed_date(entry) -> str | None:
    """RSS entry date as ISO 'YYYY-MM-DD'."""
    t = entry.get("published_parsed")
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}" if t else None


def iso_date(value: str | None) -> str | None:
    return value[:10] if value and re.match(r"\d{4}-\d{2}-\d{2}", value) else value
