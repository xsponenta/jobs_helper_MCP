"""Warning signs (and good signs) in a vacancy: staleness, reposts, vague or overloaded descriptions, pressure language,
odd apply channels, scam patterns, weak employer reputation. Every flag says why, so the candidate can judge."""

from __future__ import annotations

import json
import os
import re
from datetime import date

from jobs_helper import config
from jobs_helper.cv.review import terms
from jobs_helper.models import Job

PRESSURE = r"стресостійк|ненормован|понаднормов|у вихідні|на вихідних|24/7|overtime|on weekends|high[- ]pressure|" \
           r"fast[- ]paced environment|wear many hats|work hard,? play hard|like a family|як сім.я|дружня сім|" \
           r"готовність до переробок|багатозадачн|multitasking"
SCAM = r"оплат\w* (?:за )?навчанн|вступн\w* внес|депозит|pay for (?:the )?training|registration fee|" \
       r"без досвіду.{0,60}(?:від|до)\s*\$?\s*[3-9]\d{3}|пасивн\w* дох|криптовалютн\w* арбітраж"
ODD_CHANNEL = r"t\.me/|telegram|docs\.google\.com/forms|forms\.gle|@gmail\.com|@ukr\.net|viber"
UNPAID = r"неоплачуван|без оплати|unpaid|за власний рахунок|test task.{0,40}(?:days|днів)|тестове.{0,40}(?:\d+\s*(?:днів|дні))"
PROCESS = r"interview process|hiring process|етапи (?:співбесід|відбору)|recruitment process|процес найму|stages?:"
BENEFITS = r"paid vacation|оплачуван\w* відпуст|медичн\w* страхуван|health insurance|sick leave|лікарнян|" \
           r"education budget|бюджет на навчання|compensation for|компенсац\w* (?:курсів|навчання|спорту)"


def _seen_path():
    return config.CACHE_DIR / "seen_vacancies.json"


def _key(job: Job) -> str:
    norm = lambda s: re.sub(r"[^a-zа-яіїєґ0-9]+", " ", (s or "").lower()).strip()  # noqa: E731
    title = re.sub(r"\b(senior|middle|junior|lead|strong|sr|jr)\b\.?", " ", (job.title or "").lower())
    return f"{norm(job.company)}|{norm(title)}"


def _today() -> date:
    return date.fromisoformat(os.environ["JOBS_TODAY"]) if os.environ.get("JOBS_TODAY") else date.today()


def remember(jobs: list[Job]) -> None:
    """Keeps (company, title) → ids and first-seen date, so a vacancy posted again under a new id is recognised."""
    path = _seen_path()
    try:
        seen = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        seen = {}
    today = _today().isoformat()
    changed = False
    for j in jobs:
        if not j.company:
            continue
        e = seen.setdefault(_key(j), {"first_seen": j.published_at or today, "ids": []})
        if j.id not in e["ids"]:
            e["ids"].append(j.id)
            changed = True
        if j.published_at and j.published_at < e["first_seen"]:
            e["first_seen"], changed = j.published_at, True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")


def _seen(job: Job) -> dict | None:
    path = _seen_path()
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(_key(job)) if path.exists() else None
    except (OSError, ValueError):
        return None


def _age_days(iso: str | None) -> int | None:
    try:
        return (_today() - date.fromisoformat(iso[:10])).days if iso else None
    except ValueError:
        return None


def vacancy_flags(job: Job, company: dict | None = None) -> dict:
    red: list[dict] = []
    green: list[str] = []
    text = f"{job.title}\n{job.description}"
    low = text.lower()
    add = lambda sev, flag, why: red.append({"severity": sev, "flag": flag, "why": why})  # noqa: E731

    age = _age_days(job.published_at)
    if age is not None and age > 60:
        add("major", "stale", f"Published {age} days ago — likely filled or a pipeline-collecting post.")
    elif age is not None and age <= 7:
        green.append(f"fresh: published {age} day(s) ago")
    seen = _seen(job)
    if seen and len(seen["ids"]) > 1 and (_age_days(seen["first_seen"]) or 0) > 21:
        add("minor", "reposted", f"The same role at this company was posted before (first seen {seen['first_seen']}, "
                                 f"{len(seen['ids'])} postings) — hard to fill: high turnover or an unrealistic profile.")
    if job.salary_min or job.salary_max:
        green.append("salary is published")
    elif job.source == "djinni":
        add("minor", "no_salary", "No salary on Djinni, where most vacancies publish one.")
    stack = terms(text)
    desc_len = len(job.description.strip())
    if job.details_loaded and desc_len < 500:
        add("minor", "vague", f"Very short description ({desc_len} chars): tasks and requirements are unclear.")
    elif job.details_loaded and desc_len > 800 and not re.search(r"(?:you will|ти будеш|ви будете|обов.язки|responsibilit|tasks|задачі|завдання)", low):
        add("minor", "vague", "No responsibilities section: the actual work is not described.")
    if len(stack) >= 18:
        add("minor", "oversized_stack", f"{len(stack)} technologies listed — a 'unicorn' wish list or several roles in one.")
    elif 3 <= len(stack) <= 12:
        green.append("focused stack: " + ", ".join(sorted(stack)[:6]))
    if job.seniority in ("intern", "junior") and (job.experience_years or 0) >= 3:
        add("major", "level_mismatch", f"Called {job.seniority}, but asks for {job.experience_years:g}+ years — senior work for junior pay.")
    pressure = sorted({m.group(0) for m in re.finditer(PRESSURE, low)})
    if pressure:
        add("major" if len(pressure) >= 2 else "minor", "pressure_language", "Wording that often means overtime or chaos: " + ", ".join(pressure[:4]))
    if re.search(SCAM, low):
        add("major", "scam_pattern", "Asks for money / promises high pay without experience — typical of scams. Never pay to get a job.")
    channel = re.search(ODD_CHANNEL, f"{low} {(job.apply_url or '').lower()}")
    if channel:
        add("minor", "informal_channel", f"Applications via {channel.group(0)} — no tracking, personal data goes to a private account.")
    if re.search(UNPAID, low):
        add("major", "unpaid_work", "Unpaid trial work or a long test task is mentioned.")
    if re.search(PROCESS, low):
        green.append("hiring process is described")
    if re.search(BENEFITS, low):
        green.append("concrete benefits listed")
    if not job.company:
        add("minor", "anonymous", "Company is not named (agency or anonymous posting).")

    if company and company.get("found_on_dou"):
        r = company.get("rating") or {}
        score, votes = r.get("score_100"), r.get("votes") or 0
        if score is not None and votes >= 10 and score < 55:
            add("major", "low_employee_rating", f"DOU employee rating {score:g}/100 from {votes:g} votes.")
        elif score is not None and votes >= 10 and score >= 75:
            green.append(f"DOU employee rating {score:g}/100 ({votes:g} votes)")
        for concern in (company.get("reviews") or {}).get("concerns", [])[:3]:
            add("minor", f"reviews:{concern}", f"Recurring complaints in employee reviews about {concern.replace('_', ' ')}.")
    weight = sum(2 if f["severity"] == "major" else 1 for f in red)
    return {"risk": "high" if weight >= 4 else "medium" if weight >= 2 else "low", "red_flags": red, "green_flags": green}
