"""How well a vacancy fits the candidate: keyword coverage against the CV, plus knock-outs a recruiter or ATS applies
(years, English, seniority). Cheap enough to run on every search result."""

from __future__ import annotations

import re

from jobs_helper.cv.review import terms, timeline
from jobs_helper.extract import guess_english, guess_experience_years
from jobs_helper.models import Job

_EN = ["A1", "A2", "B1", "B2", "C1", "C2"]
# a CV that shows the key implies the values (PostgreSQL experience is SQL experience)
IMPLIES = {"postgresql": {"sql"}, "mysql": {"sql"}, "oracle": {"sql"}, "sqlite": {"sql"}, "clickhouse": {"sql"},
           "bigquery": {"sql"}, "snowflake": {"sql"}, "kubernetes": {"k8s"}, "k8s": {"kubernetes"}, "golang": {"go"},
           "go": {"golang"}, "django": {"python"}, "flask": {"python"}, "fastapi": {"python"}, "next.js": {"react"},
           "nestjs": {"node.js", "typescript"}, "spring boot": {"spring", "java"}, "asp.net": {".net", "c#"},
           "github actions": {"ci/cd"}, "gitlab ci": {"ci/cd"}, "jenkins": {"ci/cd"}, "react native": {"react"},
           "lambda": {"aws", "serverless"}, "dynamodb": {"aws"}, "scrum": {"agile"}, "kanban": {"agile"}}


def expand(skills: set[str]) -> set[str]:
    return skills | {x for s in skills for x in IMPLIES.get(s, ())}


def norm_english(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().upper()[:2]
    return v if v in _EN else guess_english(f"English {value}")
_LEVELS = ["intern", "junior", "middle", "senior", "lead"]


def candidate_context(cv_text: str, profile: dict | None = None) -> dict:
    profile = profile or {}
    tl = timeline(cv_text)
    raw = str((profile.get("languages") or {}).get("english") or "").strip()
    en = norm_english(raw) or guess_english(cv_text)
    years = tl["total_years"]
    level = "intern" if years < 0.5 else "junior" if years < 1.5 else "middle" if years < 5 else "senior"
    return {"skills": expand(terms(cv_text)), "years": years, "english": en, "level": level, "has_cv": bool(cv_text.strip())}


def fit(job: Job, ctx: dict) -> dict:
    """{'score': 0-100, 'label', 'knockouts', 'covered', 'missing'} — score is None without a CV."""
    if not ctx.get("has_cv"):
        return {"score": None, "label": "unknown", "note": "no CV to compare with"}
    text = f"{job.title}\n{' '.join(job.tags)}\n{job.description}"
    title_terms = terms(job.title) | terms(" ".join(job.tags))
    wanted = terms(text)
    weight = {t: 2 if t in title_terms else 1 for t in wanted}
    covered = sorted(wanted & ctx["skills"])
    missing = sorted(wanted - ctx["skills"], key=lambda t: (-weight[t], t))
    coverage = sum(weight[t] for t in covered) / sum(weight.values()) if wanted else 0.5
    score = 25 + 65 * coverage
    knockouts = []
    req = job.experience_years if job.experience_years is not None else guess_experience_years(job.description)
    if req and ctx["years"] + 0.5 < req:
        knockouts.append(f"needs {req:g}+ years, CV shows {ctx['years']:g}")
        score -= min(35, 12 * (req - ctx["years"]))
    want_en = norm_english(job.english) or guess_english(job.description)
    if want_en in _EN and ctx["english"] in _EN and _EN.index(ctx["english"]) < _EN.index(want_en):
        knockouts.append(f"English {want_en} required, candidate has {ctx['english']}")
        score -= 15
    if job.seniority in _LEVELS:
        gap = _LEVELS.index(job.seniority) - _LEVELS.index(ctx["level"])
        if gap >= 2:
            knockouts.append(f"{job.seniority} role, CV reads as {ctx['level']}")
            score -= 15
        elif gap <= -2:
            knockouts.append(f"{job.seniority} role is below the candidate's level ({ctx['level']})")
            score -= 8
        elif gap == 0:
            score += 5
    if wanted and not covered:
        score = min(score, 30)
    score = max(0, min(100, round(score)))
    label = "strong" if score >= 70 and not knockouts else "good" if score >= 55 else "stretch" if score >= 40 else "weak"
    return {"score": score, "label": label, "knockouts": knockouts, "covered": covered[:10], "missing": missing[:8]}


def short_with_fit(job: Job, ctx: dict) -> dict:
    f = fit(job, ctx)
    out = job.short() | {"fit": f["score"], "fit_label": f["label"]}
    if f.get("knockouts"):
        out["knockouts"] = f["knockouts"]
    if f.get("missing"):
        out["missing_skills"] = f["missing"][:5]
    return out


def tech_terms(text: str) -> list[str]:
    return sorted(terms(text))


def seniority_word(text: str) -> str | None:
    m = re.search(r"\b(intern|junior|middle|senior|lead)\b", text or "", re.I)
    return m.group(1).capitalize() if m else None
