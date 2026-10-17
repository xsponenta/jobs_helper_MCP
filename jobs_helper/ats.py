"""Which site a vacancy is applied on: the board itself, a known ATS or something we can't fill."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from jobs_helper.models import Job

ATS_PATTERNS = [
    ("greenhouse", r"(^|\.)greenhouse\.io$|^grnh\.se$"),
    ("lever", r"(^|\.)lever\.co$"),
    ("workable", r"(^|\.)workable\.com$"),
    ("bamboohr", r"(^|\.)bamboohr\.com$"),
    ("peopleforce", r"(^|\.)peopleforce\.io$"),
    ("recruitee", r"(^|\.)recruitee\.com$"),
    ("smartrecruiters", r"(^|\.)smartrecruiters\.com$"),
    ("teamtailor", r"(^|\.)teamtailor\.com$"),
    ("breezy", r"(^|\.)breezy\.hr$"),
    ("ashby", r"(^|\.)ashbyhq\.com$"),
    ("personio", r"(^|\.)personio\.(de|com)$"),
    ("workday", r"(^|\.)myworkdayjobs\.com$"),
    ("hurma", r"(^|\.)hurma\.work$"),
    ("recman", r"(^|\.)recman\.page$"),
]
NOT_A_FORM = [
    ("telegram", r"^t\.me$|^telegram\.me$"),
    ("linkedin", r"(^|\.)linkedin\.com$"),
    ("google_forms", r"^docs\.google\.com$|^forms\.gle$"),
]


def classify_url(url: str | None) -> str:
    """'ats:<name>' | 'company_site' | 'email' | 'not_a_form:<kind>' | 'unknown'."""
    if not url:
        return "unknown"
    if url.startswith("mailto:"):
        return "email"
    host = urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    for name, pat in ATS_PATTERNS:
        if re.search(pat, host):
            return f"ats:{name}"
    for name, pat in NOT_A_FORM:
        if re.search(pat, host):
            return f"not_a_form:{name}"
    return "company_site" if host else "unknown"


def apply_target(job: Job) -> str:
    """Where the application form lives:
    'dou' | 'djinni' | 'happymonday' | 'ats:<name>' | 'company_site' | 'email' | 'not_a_form:<kind>' | 'unknown'."""
    if job.apply_type == "external":
        return classify_url(job.apply_url)
    if job.apply_type in ("on_site", "login_required"):
        return job.source
    return "unknown"


def policy_site(target: str) -> str:
    """Key in settings.yaml submit_policy for a given target."""
    return target if target in ("dou", "djinni", "happymonday") else "ats"
