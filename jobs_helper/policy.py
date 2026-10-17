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
    return Path(os.environ.get("JOBS_SETTINGS", config.ROOT / "settings.yaml"))


def candidate_path() -> Path:
    return Path(os.environ.get("JOBS_CANDIDATE", config.ROOT / "candidate.yaml"))


def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}


def load_policies() -> dict[str, Policy]:
    raw = _load_yaml(settings_path()).get("submit_policy", {}) or {}
    return {site: raw.get(site) if raw.get(site) in POLICIES else DEFAULTS[site] for site in SITES}


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


def load_candidate() -> dict | None:
    data = _load_yaml(candidate_path())
    return data or None
