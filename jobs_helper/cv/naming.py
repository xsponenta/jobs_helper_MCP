"""Recruiter-friendly, unique CV file names: Olena_Kovalenko_Python_Developer_RetailPulse_CV.pdf

Latin only (Ukrainian names are transliterated by the official 2010 rules), no spaces, never overwriting an
existing file.
"""

from __future__ import annotations

import re
from pathlib import Path

_UA = {"а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z", "и": "y",
       "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
       "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "iu",
       "я": "ia", "ы": "y", "э": "e", "ё": "io", "ъ": "", "'": "", "ʼ": "", "’": ""}
_UA_START = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}


def translit(text: str) -> str:
    text = re.sub(r"зг", "zgh", text)
    text = re.sub(r"Зг", "Zgh", text)
    text = re.sub(r"ЗГ", "ZGH", text)
    out = []
    for word in re.split(r"(\s+)", text):
        for i, ch in enumerate(word):
            low = ch.lower()
            rep = (_UA_START if i == 0 else {}).get(low) or _UA.get(low)
            if rep is None:
                out.append(ch)
            else:
                out.append(rep.capitalize() if ch.isupper() and rep else rep)
    return "".join(out)


def _part(text: str | None, max_words: int = 4) -> str:
    if not text:
        return ""
    t = translit(text)
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)                       # drop "(Impulse)", "[remote]"
    words = re.findall(r"[A-Za-z0-9+#]+", t)[:max_words]
    return "_".join(w if (w.isupper() and len(w) <= 4) else w[0].upper() + w[1:] for w in words)


def cv_filename(name: str, role: str | None = None, company: str | None = None, suffix: str = ".pdf",
                directory: Path | None = None, extra: str | None = None) -> str:
    parts = [p for p in (_part(name, 3), _part(role, 4), _part(company, 3), _part(extra, 2)) if p]
    stem = "_".join(parts + ["CV"]) if parts else "CV"
    stem = stem[:110]
    candidate = f"{stem}{suffix}"
    if directory is not None:
        n = 2
        while (directory / candidate).exists():
            candidate = f"{stem}_{n}{suffix}"
            n += 1
    return candidate


def name_from_markdown(cv_markdown: str) -> str:
    return next((line[2:].strip() for line in cv_markdown.splitlines() if line.startswith("# ")), "")


def title_from_markdown(cv_markdown: str) -> str:
    lines = cv_markdown.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return re.split(r"\s[·|•]\s", nxt.strip())[0] if not nxt.startswith("#") else ""
    return ""
