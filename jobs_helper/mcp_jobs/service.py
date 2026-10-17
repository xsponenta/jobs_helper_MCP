"""Business logic behind the `jobs` MCP tools. Kept free of MCP so it can be tested directly.

Every failure is returned (never raised) as:
    {"error": <code>, "message": <what happened>, "hint": <what to do next>, "retryable": bool, ...extra}
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from jobs_helper import policy as pol
from jobs_helper.apply.backend import ApplyBackend, ApplyRequest
from jobs_helper.apply.mock import MockBackend
from jobs_helper.ats import apply_target, policy_site
from jobs_helper.models import Job, SearchQuery, SourceName
from jobs_helper.snapshot import SnapshotStore, latest_snapshot
from jobs_helper.sources import get_job as live_get_job
from jobs_helper.sources import search_all
from jobs_helper.sources.base import SourceError
from jobs_helper.tracker import Tracker

WRITER_ROLES = {"reviewer", "single_agent", "manual"}
CV_MIN, CV_MAX = 400, 8000
COVER_MIN, COVER_MAX = 150, 3000
DESCRIPTION_MAX = 5000


def err(code: str, message: str, hint: str = "", retryable: bool = False, **extra) -> dict:
    return {"error": code, "message": message, "hint": hint, "retryable": retryable, **extra}


class LiveProvider:
    name = "live"

    def search(self, q: SearchQuery) -> tuple[list[Job], dict]:
        return search_all(q)

    def get(self, job_id: str) -> Job:
        return live_get_job(job_id)


class SnapshotProvider:
    def __init__(self, path: Path):
        self.store = SnapshotStore(path)
        self.name = f"snapshot:{path.name}"

    def search(self, q: SearchQuery) -> tuple[list[Job], dict]:
        return self.store.search(q), {}

    def get(self, job_id: str) -> Job:
        if job_id.startswith(("li:", "ext:")):
            return live_get_job(job_id)
        return self.store.get(job_id)


def provider_from_env():
    snap = os.environ.get("JOBS_SNAPSHOT")
    if not snap:
        return LiveProvider()
    path = latest_snapshot() if snap == "latest" else Path(snap)
    if not path or not path.exists():
        raise RuntimeError(f"JOBS_SNAPSHOT={snap!r}: snapshot file not found")
    return SnapshotProvider(path)


def backend_from_env() -> ApplyBackend:
    kind = os.environ.get("JOBS_APPLY_BACKEND", "mock")
    if kind == "mock":
        return MockBackend()
    if kind == "browser":
        from jobs_helper.apply.browser import BrowserBackend
        return BrowserBackend()
    raise RuntimeError(f"JOBS_APPLY_BACKEND={kind!r}: use 'mock' or 'browser'")


def today() -> date:
    return date.fromisoformat(os.environ["JOBS_TODAY"]) if os.environ.get("JOBS_TODAY") else date.today()


class JobsService:
    def __init__(self, provider=None, backend: ApplyBackend | None = None, tracker: Tracker | None = None,
                 role: str | None = None):
        self.provider = provider or provider_from_env()
        self.backend = backend or backend_from_env()
        self.tracker = tracker or Tracker()
        self.role = role or os.environ.get("AGENT_ROLE", "manual")

    # ------------------------------------------------------------ search_jobs
    def search_jobs(self, keywords: str, sources: list[SourceName] | None = None, remote: bool | None = None,
                    city: str | None = None, min_salary: int | None = None,
                    max_experience_years: float | None = None, limit: int = 10, sort_by: str = "relevance") -> dict:
        if not keywords or not keywords.strip():
            return err("invalid_argument", "keywords is empty.", "Pass a role and key skills, e.g. 'python django'.", True)
        if not 1 <= limit <= 20:
            return err("invalid_argument", f"limit={limit} is out of range.", "Use 1..20.", True)
        if min_salary is not None and min_salary < 0:
            return err("invalid_argument", "min_salary must be >= 0.", "Monthly USD, e.g. 3000.", True)
        if sort_by not in ("relevance", "fit", "date"):
            return err("invalid_argument", f"sort_by={sort_by!r}.", "Use relevance, fit or date.", True)
        q = SearchQuery(keywords=keywords, sources=sources or ["dou", "djinni", "happymonday"], remote=remote,
                        city=city, min_salary=min_salary, max_experience_years=max_experience_years,
                        limit=limit if sort_by == "relevance" else min(3 * limit, 60))
        jobs, errors = self.provider.search(q)
        jobs = _dedupe([j for j in jobs if self._is_open(j)])
        _remember(jobs)
        ctx = self._fit_context()
        rows = [_short(j, ctx) for j in jobs]
        if sort_by == "fit" and ctx.get("has_cv"):
            rows.sort(key=lambda r: (r.get("fit") or 0), reverse=True)
        elif sort_by == "date":
            published = {j.id: j.published_at or "" for j in jobs}
            rows.sort(key=lambda r: published[r["job_id"]], reverse=True)
        rows = rows[:limit]
        out = {"count": len(rows), "data_source": self.provider.name, "sorted_by": sort_by, "jobs": rows}
        if ctx.get("has_cv"):
            out["fit_note"] = ("fit 0-100 compares each vacancy with the candidate's CV (skills, years, English, level); "
                               "knockouts are filters a recruiter or ATS would reject on.")
        if errors:
            out["source_errors"] = errors
        if not rows:
            out["hint"] = "Nothing matched. Try broader keywords, fewer filters or other sources."
        return out

    def _fit_context(self) -> dict:
        try:
            from jobs_helper.insights.fit import candidate_context
            profile = pol.load_candidate() or {}
            return candidate_context(pol.current_cv_text(profile), profile)
        except Exception:  # noqa: BLE001 — fit is a bonus, never a reason to fail a search
            return {}

    # ------------------------------------------------------------ get_job
    def get_job(self, job_id: str) -> dict:
        try:
            job = self.provider.get((job_id or "").strip())
        except SourceError as e:
            return err(e.code, e.message, e.hint, e.code in ("invalid_job_id", "source_unavailable"))
        data = job.model_dump(exclude={"fetched_at", "details_loaded"})
        if len(data["description"]) > DESCRIPTION_MAX:
            data["description"] = data["description"][:DESCRIPTION_MAX] + "…"
        data["is_open"] = self._is_open(job)
        data["apply_target"] = apply_target(job)
        try:
            from jobs_helper.insights.fit import fit
            from jobs_helper.insights.redflags import vacancy_flags
            _remember([job])
            ctx = self._fit_context()
            if ctx.get("has_cv"):
                data["fit"] = fit(job, ctx)
            data["warnings"] = vacancy_flags(job)
        except Exception:  # noqa: BLE001
            pass
        if not data["is_open"]:
            data["similar_open_jobs"] = self._similar(job)
        return data

    # ------------------------------------------------------------ apply
    def _draft_path(self, job_id: str) -> Path:
        return self.tracker.root / "drafts" / (re.sub(r"[^\w.-]+", "_", job_id) + ".json")

    def apply(self, job_id: str, cv_markdown: str | None = None, cover_letter: str | None = None,
              answers: dict[str, str] | None = None, attach_original_cv: bool = False) -> dict:
        job_id = (job_id or "").strip()
        # a retry after needs_input / a failure may send only the answers: reuse the last draft for this job
        draft_path = self._draft_path(job_id) if job_id else None
        draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path and draft_path.exists() else {}
        cv_markdown = cv_markdown or draft.get("cv_markdown")
        cover_letter = cover_letter or draft.get("cover_letter")
        answers = {**draft.get("answers", {}), **(answers or {})}
        if not cv_markdown or not cover_letter:
            return err("missing_documents", "cv_markdown and cover_letter are required on the first apply for a job.",
                       "Send both documents; later retries for the same job_id may send only answers.", True)
        if self.role not in WRITER_ROLES:
            return err("forbidden", f"Role '{self.role}' may not submit applications.",
                       "Only the reviewer agent calls apply; hand the draft over instead.")
        profile = pol.load_candidate()
        name = _full_name(profile)
        if not profile or not name or not (profile.get("personal") or {}).get("email"):
            return err("candidate_profile_missing", "candidate.yaml is missing or has no name / email.",
                       "Ask the candidate to fill candidate.yaml (template: 01_recon/candidate.example.yaml).")
        cv, cover = (cv_markdown or "").strip(), (cover_letter or "").strip()
        if len(cv) < CV_MIN:
            return err("cv_too_short", f"CV has {len(cv)} chars, minimum is {CV_MIN}.", "Send the full tailored CV in markdown.", True)
        if len(cv) > CV_MAX:
            return err("cv_too_long", f"CV has {len(cv)} chars, maximum is {CV_MAX} (~2 pages).",
                       "Shorten the summary and older experience, then retry.", True)
        if len(cover) < COVER_MIN:
            return err("cover_letter_too_short", f"Cover letter has {len(cover)} chars, minimum is {COVER_MIN}.",
                       "Write 3-6 sentences specific to this vacancy.", True)
        if len(cover) > COVER_MAX:
            return err("cover_letter_too_long", f"Cover letter has {len(cover)} chars, maximum is {COVER_MAX}.",
                       "Keep it under ~400 words.", True)

        try:
            job = self.provider.get((job_id or "").strip())
        except SourceError as e:
            return err(e.code, e.message, e.hint, e.code == "invalid_job_id")
        if not self._is_open(job):
            similar = self._similar(job)
            alt = ", ".join(f"{s['job_id']} ({s['title']}, {s['company']})" for s in similar) or "none found"
            return err("job_closed", f"{job.id} ({job.title}, {job.company}) is closed (valid through {job.valid_through}).",
                       f"Similar open jobs: {alt}", True, similar_open_jobs=similar)

        target = apply_target(job)
        if target == "not_a_form:linkedin":
            return err("external_apply", f"{job.id} is applied on LinkedIn (Easy Apply), inside the candidate's own account.",
                       "Automating LinkedIn is against its rules: give the candidate the link and the tailored CV / letter "
                       "(render_cv) to attach there.", False, url=job.url)
        if target == "email" or target.startswith("not_a_form") or target == "unknown":
            return err("external_apply", f"{job.id} is applied via {target}, which cannot be filled automatically.",
                       "Give the candidate the link and the prepared documents.", False, url=job.apply_url or job.url)
        prev = self.tracker.find_active(name, job.id)
        if prev:
            return err("already_applied", f"{name} already applied to {job.id} on {prev['created_at'][:10]} "
                       f"({prev['application_id']}, status {prev['status']}).", "Do not re-submit; pick another job.", False)

        site = policy_site(target)
        policy = pol.load_policies()[site]
        dry = pol.dry_run()
        original = pol.cv_file(profile) if attach_original_cv else None
        if attach_original_cv and not original:
            return err("cv_file_missing", "attach_original_cv was requested but no CV file is configured.",
                       "Set the CV file in the profile, or apply with the generated CV (attach_original_cv=false).", True)
        if draft_path:
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(json.dumps({"cv_markdown": cv, "cover_letter": cover, "answers": answers},
                                             ensure_ascii=False), encoding="utf-8")
        outcome = self.backend.apply(ApplyRequest(job=job, target=target, profile=profile, cv_markdown=cv,
                                                  cover_letter=cover, answers=answers, policy=policy, dry_run=dry,
                                                  original_cv=str(original) if original else None))
        if outcome.status == "needs_input":
            return err("needs_input", outcome.message, "Answer each question from the CV / profile / saved candidate notes and call "
                       "apply again with the same job_id and answers={field_id: value} only (the documents are kept). "
                       "Questions with human_only=true must be asked to the candidate; never invent answers.",
                       True, questions=outcome.questions, filled=outcome.filled)

        app_id = self.tracker.next_id()
        cv_path, cover_path = self.tracker.save_documents(app_id, cv, cover)
        self.tracker.add(application_id=app_id, candidate=name, job_id=job.id, source=job.source, target=target,
                         company=job.company or "", title=job.title, url=job.url,
                         status="prepared" if outcome.status == "prepared" else outcome.status,
                         policy="dry_run" if dry else policy, cv_path=cv_path, cover_path=cover_path,
                         screenshot=outcome.screenshot or "", submitted_by=self.role, answers=answers or {})
        if outcome.status != "failed":
            try:
                from jobs_helper import pipeline
                pipeline.on_apply(app_id, {"job_id": job.id, "company": job.company or "", "title": job.title, "url": job.url,
                                           "source": job.source, "target": target, "policy": "dry_run" if dry else policy,
                                           "cv_variant": "original_file" if original else f"generated:{pol.cv_settings()['template']}"},
                                  outcome.status, self.tracker.root)
            except Exception:  # noqa: BLE001 — the tracker row is already written
                pass
        if outcome.status == "failed":
            return (outcome.error or err("apply_failed", outcome.message, "Hand the documents to the candidate.")) | {
                "application_id": app_id, "recorded_as": "failed (nothing was sent)"}
        if outcome.status in ("submitted", "awaiting_user") and draft_path and draft_path.exists():
            draft_path.unlink()
        return {"ok": True, "application_id": app_id, "status": outcome.status, "job_id": job.id,
                "target": target, "policy": "dry_run" if dry else policy, "filled": outcome.filled,
                "message": outcome.message, **outcome.extra}

    # ------------------------------------------------------------ helpers
    def _is_open(self, job: Job) -> bool:
        if not job.valid_through:
            return True
        try:
            return date.fromisoformat(job.valid_through[:10]) >= today()
        except ValueError:
            return True

    def _similar(self, job: Job, k: int = 3) -> list[dict]:
        try:
            found, _ = self.provider.search(SearchQuery(keywords=job.title, limit=k + 3))
        except Exception:  # noqa: BLE001 — a hint is optional
            return []
        return [j.short() for j in found if j.id != job.id and self._is_open(j)][:k]


def _dedupe(jobs: list[Job]) -> list[Job]:
    """The same vacancy is often cross-posted on several boards: keep the first (best-ranked) copy."""
    from jobs_helper.insights.redflags import _key
    seen, out = set(), []
    for j in jobs:
        k = _key(j) if j.company else j.id
        if k not in seen:
            seen.add(k)
            out.append(j)
    return out


def _remember(jobs: list[Job]) -> None:
    try:
        from jobs_helper.insights.redflags import remember
        remember(jobs)
    except Exception:  # noqa: BLE001
        pass


def _short(job: Job, ctx: dict) -> dict:
    if not ctx.get("has_cv"):
        return job.short()
    from jobs_helper.insights.fit import short_with_fit
    return short_with_fit(job, ctx)


def _full_name(profile: dict | None) -> str:
    p = (profile or {}).get("personal") or {}
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
