"""Per-site submit policy chosen by the human (settings.yaml) and the candidate profile (candidate.yaml).

The agent never chooses how an application is sent: the MCP server reads this file itself."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml

from jobs_helper import config

Policy = Literal["manual", "confirm", "auto"]
POLICIES: tuple[Policy, ...] = ("manual", "confirm", "auto")
SITES = ("dou", "djinni", "happymonday", "ats", "linkedin")
DEFAULTS: dict[str, Policy] = {"dou": "manual", "djinni": "confirm", "happymonday": "confirm", "ats": "confirm",
                               "linkedin": "confirm"}

SITE_NOTES = {
    "dou": "Правила DOU забороняють «інструменти для автоматичних відгуків» і масові відгуки "
           "(dou.ua/applications-rules); за порушення можуть заблокувати профіль.",
    "djinni": "Окремої заборони автоматизації для кандидатів не знайдено.",
    "happymonday": "Окремої заборони автоматизації для кандидатів не знайдено.",
    "ats": "Сайти компаній / ATS: можлива капча; правила DOU тут не діють.",
    "linkedin": "Публікація через офіційне API (Share on LinkedIn).",
}


def settings_path() -> Path:
    return Path(os.environ.get("JOBS_SETTINGS", config.HOME / "settings.yaml"))


def candidate_path() -> Path:
    return Path(os.environ.get("JOBS_CANDIDATE", config.HOME / "candidate.yaml"))


def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}


def load_policies() -> dict[str, Policy]:
    """settings.yaml, overridden by JOBS_POLICY_<SITE> (how the Claude Desktop extension passes its form)."""
    raw = _load_yaml(settings_path()).get("submit_policy", {}) or {}
    out = {}
    for site in SITES:
        env = os.environ.get(f"JOBS_POLICY_{site.upper()}", "").strip().lower()
        value = env if env in POLICIES else raw.get(site)
        out[site] = value if value in POLICIES else DEFAULTS[site]
    return out


def explicit_policies() -> dict[str, str]:
    """Only the sites the human has actually chosen for."""
    raw = _load_yaml(settings_path()).get("submit_policy", {}) or {}
    return {k: v for k, v in raw.items() if v in POLICIES}


def set_policy(site: str, policy: Policy) -> None:
    if site not in SITES or policy not in POLICIES:
        raise ValueError(f"site must be one of {SITES}, policy one of {POLICIES}")
    path = settings_path()
    data = _load_yaml(path)
    data.setdefault("submit_policy", {})[site] = policy
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def dry_run() -> bool:
    return os.environ.get("JOBS_DRY_RUN", "").lower() in ("1", "true", "yes")


# environment variable -> (section, key); used by the Claude Desktop extension form
ENV_PROFILE = {
    "JOBS_CANDIDATE_FIRST_NAME": ("personal", "first_name"), "JOBS_CANDIDATE_LAST_NAME": ("personal", "last_name"),
    "JOBS_CANDIDATE_EMAIL": ("personal", "email"), "JOBS_CANDIDATE_PHONE": ("personal", "phone"),
    "JOBS_CANDIDATE_CITY": ("personal", "city"), "JOBS_CANDIDATE_COUNTRY": ("personal", "country"),
    "JOBS_CANDIDATE_LINKEDIN": ("links", "linkedin"), "JOBS_CANDIDATE_GITHUB": ("links", "github"),
    "JOBS_CANDIDATE_PORTFOLIO": ("links", "portfolio"), "JOBS_CANDIDATE_TELEGRAM": ("links", "telegram"),
    "JOBS_CANDIDATE_SALARY_USD": ("job_preferences", "desired_salary_usd"),
    "JOBS_CANDIDATE_NOTICE": ("job_preferences", "notice_period"),
    "JOBS_CANDIDATE_ENGLISH": ("languages", "english"),
    "JOBS_CANDIDATE_CV": (None, "cv_file"),
    "JOBS_CANDIDATE_CONSENT": ("answers", "consent_to_processing"),
}


def load_candidate() -> dict | None:
    """candidate.yaml overlaid with JOBS_CANDIDATE_* variables; empty values are ignored."""
    data = _load_yaml(candidate_path())
    for var, (section, key) in ENV_PROFILE.items():
        raw = os.environ.get(var, "").strip()
        if not raw or raw.startswith("${"):          # unset extension fields may arrive as literal placeholders
            continue
        value: object = raw
        if key == "desired_salary_usd":
            try:
                value = int(float(raw))
            except ValueError:
                continue
        if key == "consent_to_processing":
            value = raw.lower() in ("1", "true", "yes", "так")
        (data.setdefault(section, {}) if section else data)[key] = value
    return data or None


CV_NAMES = ("cv.md", "cv.txt", "cv.pdf", "cv.docx")


def cv_file(profile: dict | None = None) -> Path | None:
    profile = profile if profile is not None else (load_candidate() or {})
    if profile.get("cv_file"):
        path = Path(str(profile["cv_file"])).expanduser()
        if not path.is_absolute():
            path = candidate_path().parent / path
        return path if path.exists() else None
    for name in CV_NAMES:
        path = config.HOME / name
        if path.exists():
            return path
    return None


def read_cv_text(path: Path) -> str:
    """Plain text of a CV in .md / .txt / .pdf / .docx."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages).strip()
    if suffix == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(str(path)).paragraphs).strip()
    return path.read_text(errors="replace").strip()
