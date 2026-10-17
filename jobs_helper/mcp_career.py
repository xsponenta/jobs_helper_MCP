"""Career tools of the all-in-one server: application pipeline, saved searches, salary data, company research,
interview preparation and market skill gaps. Plain functions — mcp_all registers them as MCP tools."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field

from jobs_helper import policy as pol
from jobs_helper.mcp_jobs import server as jobs_server
from jobs_helper.mcp_jobs.service import err


def _job(job_id: str):
    """(Job, error dict)"""
    from jobs_helper.sources.base import SourceError
    try:
        return jobs_server.get_service().provider.get((job_id or "").strip()), None
    except SourceError as e:
        return None, err(e.code, e.message, e.hint, e.code in ("invalid_job_id", "source_unavailable"))


def _guard(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — tools never crash the server
        return err("internal_error", f"{type(e).__name__}: {e}", "Retry once; if it fails again, report it.", True)


# ---------------------------------------------------------------- pipeline

def update_application(
    application_id: Annotated[str | None, Field(description="Id from apply / pipeline_report (A-0003, M-0001). Omit to log an application the candidate made elsewhere (then company and title are required)")] = None,
    stage: Annotated[Literal["submitted", "screening", "interview", "test_task", "offer", "accepted", "rejected", "withdrawn", "ghosted"] | None,
                     Field(description="New stage, e.g. screening after a recruiter replied, interview when one is scheduled")] = None,
    note: Annotated[str | None, Field(description="What happened, e.g. 'Recruiter Olena called, tech interview next week'")] = None,
    contact_name: Annotated[str | None, Field(description="Recruiter / hiring manager name")] = None,
    contact_role: Annotated[str | None, Field(description="Their role")] = None,
    contact_email: Annotated[str | None, Field(description="Their email or LinkedIn")] = None,
    next_step: Annotated[str | None, Field(description="What comes next, e.g. 'Technical interview'. Empty string clears it")] = None,
    next_step_date: Annotated[str | None, Field(description="YYYY-MM-DD of the next step")] = None,
    company: Annotated[str | None, Field(description="For a new manual entry")] = None,
    title: Annotated[str | None, Field(description="For a new manual entry")] = None,
    url: Annotated[str | None, Field(description="For a new manual entry")] = None,
    source: Annotated[str | None, Field(description="For a new manual entry: linkedin, referral, company_site, …")] = None,
) -> dict[str, Any]:
    """UPDATE the application pipeline (state-changing): move an application to a new stage, add a note, a contact or
    the next step with a date — or log an application made outside this tool (LinkedIn Easy Apply, referral).
    Update it whenever the candidate reports news. If a Gmail / email connector is available, read replies from
    companies and propose the updates to the candidate first."""
    from jobs_helper import pipeline

    def run():
        contact = {"name": contact_name, "role": contact_role, "email": contact_email}
        try:
            e = pipeline.update(application_id, stage, note, contact, next_step, next_step_date, company, title, url, source)
        except KeyError:
            return err("not_found", f"No application {application_id}.", "Use pipeline_report to list ids.")
        except ValueError as x:
            return err("invalid_argument", str(x), "Fix the argument and retry.", True)
        return {"ok": True, "application": e}
    return _guard(run)


def pipeline_report() -> dict[str, Any]:
    """The candidate's job-search pipeline: applications by stage, the funnel (response and interview rates), rates
    by board, by application form and by CV variant (which CV works better), follow-ups that are due, applications
    that look ghosted, browser applications still waiting for the candidate's Submit, and upcoming interviews."""
    from jobs_helper import pipeline
    return _guard(pipeline.report)


# ---------------------------------------------------------------- saved searches

def saved_search(
    action: Annotated[Literal["save", "list", "check", "delete"], Field(description="save a query; list saved ones; check = run and return only vacancies new since the last check; delete")],
    name: Annotated[str | None, Field(description="Name of the saved search, e.g. 'python remote'. check without a name checks all")] = None,
    keywords: Annotated[str | None, Field(description="For save: role and key skills")] = None,
    sources: Annotated[list[Literal["dou", "djinni", "happymonday"]] | None, Field(description="For save: boards")] = None,
    remote: Annotated[bool | None, Field(description="For save: remote only")] = None,
    city: Annotated[str | None, Field(description="For save: city")] = None,
    min_salary: Annotated[int | None, Field(description="For save: monthly USD")] = None,
    max_experience_years: Annotated[float | None, Field(description="For save: the candidate's experience")] = None,
) -> dict[str, Any]:
    """Saved searches ("what's new"): store the candidate's standing queries and, on check, return only vacancies that
    appeared since the last check, sorted by fit with the CV. Checking marks them as seen."""
    from jobs_helper import searches

    def run():
        data = searches.load()
        if action == "list":
            return {"saved": [{"name": k, "params": v["params"], "last_checked": v["last_checked"]} for k, v in data.items()]}
        if action == "save":
            if not (name and keywords):
                return err("invalid_argument", "save needs name and keywords.", "Pass both.", True)
            e = searches.save_search(name, {"keywords": keywords, "sources": sources, "remote": remote, "city": city,
                                            "min_salary": min_salary, "max_experience_years": max_experience_years})
            return {"ok": True, "name": name, "params": e["params"], "hint": "Use saved_search(action='check') later to see new vacancies."}
        if action == "delete":
            return {"ok": True} if name and searches.delete(name) else err("not_found", f"No saved search '{name}'.", "Use action='list'.")
        names = [name] if name else list(data)
        if name and name not in data:
            return err("not_found", f"No saved search '{name}'.", "Use action='list'.")
        if not names:
            return {"results": [], "hint": "No saved searches yet: save one with action='save'."}
        results = []
        for n in names:
            e = data[n]
            res = jobs_server.get_service().search_jobs(**e["params"], limit=20, sort_by="fit")
            if "error" in res:
                results.append({"name": n, "error": res["error"], "message": res["message"]})
                continue
            first = e["last_checked"] is None
            new = [j for j in res["jobs"] if j["job_id"] not in set(e["seen_ids"])]
            searches.mark_seen(n, [j["job_id"] for j in res["jobs"]])
            results.append({"name": n, "since": e["last_checked"] or "first check", "new_count": len(new), "new": new,
                            **({"note": "First check: every current vacancy counts as new."} if first else {})})
        return {"results": results}
    return _guard(run)


# ---------------------------------------------------------------- salary

_LEVEL_WORDS = {"intern": "Intern", "junior": "Junior", "middle": "Middle", "senior": "Senior", "lead": "Lead"}


def salary_insights(
    role: Annotated[str | None, Field(description="Role or category, e.g. 'QA', 'Project Manager', 'DevOps', 'Python'")] = None,
    level: Annotated[Literal["junior", "middle", "senior", "lead", "head"] | None, Field(description="Seniority")] = None,
    language: Annotated[str | None, Field(description="Main programming language for developers, e.g. 'Python', 'JavaScript', 'C#'")] = None,
    sphere: Annotated[str | None, Field(description="Business sphere, e.g. 'Fintech', 'GameDev', 'E-commerce', 'Gambling'")] = None,
    company_type: Annotated[Literal["Продуктова", "Сервісна", "Аутстафінгова", "Стартап"] | None, Field(description="Product / service / outstaffing / startup")] = None,
    min_years: Annotated[float | None, Field(description="Experience from, years")] = None,
    max_years: Annotated[float | None, Field(description="Experience up to, years")] = None,
    job_id: Annotated[str | None, Field(description="A vacancy: its role, level and language are used, and its posted salary is compared with the market")] = None,
) -> dict[str, Any]:
    """Salary ranges in Ukrainian IT from DOU's open salary survey (net USD / month: p25, median, p75, p90) with splits
    by level, company type and business sphere. Use it to judge an offer or a vacancy's salary, or to answer
    salary-expectation questions."""
    from jobs_helper.insights import salary as sal
    from jobs_helper.insights.fit import tech_terms

    def run():
        r, lv, lang, job = role, level, language, None
        if job_id:
            job, e = _job(job_id)
            if e:
                return e
            lv = lv or (job.seniority if job.seniority in _LEVEL_WORDS else None)
            if not lang:
                lang = next((sal.LANG_ALIASES[t] for t in tech_terms(job.title) if t in sal.LANG_ALIASES), None)
            if not (r or lang):
                r = re.sub(r"(?i)\b(senior|middle|junior|lead|strong|sr|jr|developer|engineer|розробник)\b", " ", job.title).strip()
        out = sal.query(role=r, level=lv, language=lang, sphere=sphere, company_type=company_type, min_years=min_years, max_years=max_years)
        if out["overall"]["n"] == 0 and r and lang:
            out = sal.query(level=lv, language=lang, sphere=sphere, company_type=company_type, min_years=min_years, max_years=max_years)
        if job is not None:
            posted = job.salary_max or job.salary_min
            o = out["overall"]
            out["vacancy"] = {"job_id": job.id, "title": job.title, "posted_salary": [job.salary_min, job.salary_max]}
            if posted and o.get("n"):
                pos = "below p25" if posted < o["p25"] else "p25-median" if posted < o["median"] else \
                      "median-p75" if posted < o["p75"] else "above p75"
                out["vacancy"]["position_in_market"] = pos
        out["note"] = "Survey answers are net monthly income; vacancy salaries are usually gross or 'on hand' — compare carefully."
        return out
    return _guard(run)


# ---------------------------------------------------------------- company

def company_insights(
    company: Annotated[str | None, Field(description="Company name as in the vacancy")] = None,
    job_id: Annotated[str | None, Field(description="Or a vacancy id: its company is researched and the vacancy's red flags are added")] = None,
) -> dict[str, Any]:
    """RESEARCH an employer on DOU: size, offices, main business and spheres, Diia City, employee rating with
    sub-scores (compensation, conditions, career, project, culture), and an analysis of employee reviews — themes
    (pay, management, workload, culture, processes, layoffs, hiring process, …) with tone, recurring strengths and
    concerns, and short quotes. With job_id also returns the vacancy's red / green flags."""
    from jobs_helper.insights.company import company_insights as research
    from jobs_helper.insights.redflags import vacancy_flags

    def run():
        job = None
        if job_id:
            job, e = _job(job_id)
            if e:
                return e
        name = company or (job.company if job else None)
        if not name and not (job_id or "").startswith("dou:"):
            return err("invalid_argument", "Pass company or job_id.", "E.g. company='Ajax Systems'.", True)
        out = research(name, job_id, job.description if job else "")
        if job is not None:
            out["vacancy_flags"] = vacancy_flags(job, out)
        return out
    return _guard(run)


# ---------------------------------------------------------------- interview / market

def interview_prep(
    job_id: Annotated[str, Field(description="Vacancy id (from search_jobs, get_job or read_vacancy)")],
    include_company: Annotated[bool, Field(description="Add a company brief from DOU (rating, what candidates say about interviews)")] = True,
) -> dict[str, Any]:
    """PREPARE the candidate for an interview for a vacancy: key requirements, likely technical topics per technology,
    behavioral questions, risk questions from the CV (gaps, short jobs, missing skills, years), the candidate's
    strongest facts for STAR answers, a company brief and questions to ask. Then offer a mock interview."""
    from jobs_helper.insights.company import company_insights as research
    from jobs_helper.insights.interview import prepare

    def run():
        job, e = _job(job_id)
        if e:
            return e
        profile = pol.load_candidate() or {}
        cv = pol.current_cv_text(profile)
        notes = "\n".join(f"- {n['text']}" for n in pol.list_notes() if n["kind"] == "fact")
        comp = None
        if include_company and job.company:
            try:
                comp = research(job.company, job.id, job.description)
            except Exception:  # noqa: BLE001 — the brief is optional
                comp = None
        out = prepare(job, f"{cv}\n{notes}", profile, comp)
        if not cv:
            out["hint"] = "No CV found: risk questions and STAR material need the candidate's CV (get_candidate)."
        return out
    return _guard(run)


def market_skill_gap(
    role: Annotated[str, Field(description="Target role with its main technology, e.g. 'python developer', 'qa automation', 'project manager'")],
    sources: Annotated[list[Literal["dou", "djinni", "happymonday"]] | None, Field(description="Boards; default all")] = None,
    remote: Annotated[bool | None, Field(description="Remote vacancies only")] = None,
    seniority: Annotated[Literal["junior", "middle", "senior", "lead"] | None, Field(description="Target level")] = None,
    sample: Annotated[int, Field(description="How many current vacancies to analyse, 10..60")] = 40,
) -> dict[str, Any]:
    """ANALYSE the market for a target role: which technologies current vacancies ask for and how often, which of them
    the candidate's CV already shows, the gaps ranked by demand (with related skills the candidate has), level /
    English / remote mix, posted salaries plus the DOU salary survey, and a learning plan."""
    from jobs_helper.insights import salary as sal
    from jobs_helper.insights.fit import tech_terms
    from jobs_helper.insights.market import analyze
    from jobs_helper.models import SearchQuery

    def run():
        if not (role or "").strip():
            return err("invalid_argument", "role is empty.", "E.g. 'python developer'.", True)
        n = max(10, min(sample, 60))
        q = SearchQuery(keywords=role, sources=sources or ["dou", "djinni", "happymonday"], remote=remote, seniority=seniority, limit=n)
        jobs, errors = jobs_server.get_service().provider.search(q)
        profile = pol.load_candidate() or {}
        cv = pol.current_cv_text(profile)
        survey = None
        try:
            lang = next((sal.LANG_ALIASES[t] for t in tech_terms(role) if t in sal.LANG_ALIASES), None)
            s = sal.query(role=None if lang else role, language=lang, level=seniority)
            survey = {"median": s["overall"].get("median"), "p25": s["overall"].get("p25"), "p75": s["overall"].get("p75"),
                      "n": s["overall"]["n"], "source": s["source"]}
        except Exception:  # noqa: BLE001
            pass
        out = analyze(jobs, cv, survey)
        if errors:
            out["source_errors"] = errors
        if not cv:
            out["hint"] = "No CV found: showing market demand only. Add a CV to see the candidate's gaps."
        return out
    return _guard(run)


# ---------------------------------------------------------------- tables, CV history, dashboard

_COLUMNS = {
    "applications": ["application_id", "date", "company", "title", "source", "sent_how", "status_ua", "fit", "cv_version", "next_step", "files"],
    "vacancies": ["last_seen", "company", "title", "source", "salary", "fit", "risk", "opened", "applied", "status"],
    "cv_history": ["version", "date", "reason", "score", "level", "template", "words", "used_in", "current"],
    "activity": ["week_of", "found", "opened", "applied", "responses", "interviews", "offers", "rejections", "applied_by_source"],
}


def job_search_tables(
    table: Annotated[Literal["applications", "vacancies", "cv_history", "activity"],
                     Field(description="applications: when and where the candidate applied through the assistant (+ logged manually); "
                                       "vacancies: what the assistant found / opened, with fit and red flags; cv_history: versions of "
                                       "the main CV; activity: per-week counts")],
    days: Annotated[int | None, Field(description="Only the last N days (applications / vacancies)")] = None,
    source: Annotated[str | None, Field(description="applications: only this board, e.g. 'djinni'")] = None,
    status: Annotated[str | None, Field(description="applications: only this stage, e.g. 'interview'")] = None,
    only_opened: Annotated[bool, Field(description="vacancies: only ones opened in full, not just seen in search")] = False,
    min_fit: Annotated[int | None, Field(description="vacancies: only fit >= this")] = None,
    limit: Annotated[int, Field(description="Max rows, 1..100")] = 30,
) -> dict[str, Any]:
    """TABLES of the candidate's job search, ready to show in chat as a markdown table (use the column order given).
    For a full interactive view with filters and links to the sent files, use open_dashboard."""
    from jobs_helper import activity, dashboard

    def run():
        n = max(1, min(limit, 100))
        if table == "applications":
            rows = activity.applications(days, source, status)
            for r in rows:
                r["files"] = ", ".join(sorted(r.get("files") or {}))
        elif table == "vacancies":
            rows = activity.vacancies(days, only_opened, min_fit)
        elif table == "cv_history":
            rows = dashboard.collect()["cv"]
        else:
            rows = activity.weekly()[::-1]
        cols = _COLUMNS[table]
        out = {"table": table, "total": len(rows), "columns": cols, "rows": [{c: r.get(c) for c in cols} for r in rows[:n]]}
        if not rows:
            out["hint"] = {"applications": "No applications yet.", "vacancies": "No vacancies logged yet: search first.",
                           "cv_history": "No CV versions yet: they appear when an improved CV is made default.",
                           "activity": ""}[table]
        else:
            out["hint"] = "Show as a markdown table with short Ukrainian column titles; offer open_dashboard for the full view."
        return out
    return _guard(run)


def cv_history(
    action: Annotated[Literal["list", "show", "compare", "restore"], Field(description="list versions; show one version's text; compare two; restore an older one as the main CV")],
    version: Annotated[int | None, Field(description="Version number (v0 = the original file)")] = None,
    other_version: Annotated[int | None, Field(description="compare: the second version (default: the one before `version`)")] = None,
) -> dict[str, Any]:
    """HISTORY of the candidate's main CV: every version that became the default, with date, reason, score, level,
    design and how many applications used it; show a version, compare two (score, technologies and lines added /
    removed), or restore an older version (state-changing; nothing is lost — the restore becomes a new version)."""
    from jobs_helper import cv_history as hist
    from jobs_helper import dashboard

    def run():
        if action == "list":
            items = dashboard.collect()["cv"]
            return {"versions": items, "current": hist.current_version()} if items else \
                {"versions": [], "hint": "No CV yet: add one in the settings or create it with create_cv."}
        if version is None:
            return err("invalid_argument", f"{action} needs version.", "Use action='list' to see version numbers.", True)
        e = hist.get(version)
        if not e:
            return err("not_found", f"No CV version v{version}.", "Use action='list'.")
        if action == "show":
            return {"version": version, **{k: e.get(k) for k in ("created_at", "reason", "score", "level", "template", "pdf")},
                    "text": hist.text_of(e)[:12000]}
        if action == "compare":
            other = other_version if other_version is not None else max(version - 1, 0)
            try:
                return hist.compare(other, version)
            except KeyError as x:
                return err("not_found", f"No CV version v{x.args[0]}.", "Use action='list'.")
        res = hist.restore(version)
        return {"ok": True, **res, "message": f"Version v{version} is the main CV again; it will be used for new applications."}
    return _guard(run)


def open_dashboard(
    open_in_browser: Annotated[bool, Field(description="Open the page in the default browser")] = True,
) -> dict[str, Any]:
    """OPEN the candidate's job-search dashboard: a local page with tabs Applications (date, company, vacancy, board,
    status, fit, CV version, links to the exact CV / letter PDFs and screenshot that were sent), Vacancies (found /
    opened, fit, red flags, applied or not), CV history and Activity per week, with filters and sorting. The file is
    rebuilt on every call and stays on the candidate's computer."""
    from jobs_helper import dashboard

    def run():
        path = dashboard.build()
        opened = False
        if open_in_browser:
            import webbrowser
            try:
                opened = webbrowser.open(path.resolve().as_uri())
            except Exception:  # noqa: BLE001
                opened = False
        return {"ok": True, "path": str(path), "opened": opened,
                "hint": "Tell the candidate the page is open" if opened else f"Ask the candidate to open {path} in a browser"}
    return _guard(run)


TOOLS = (update_application, pipeline_report, saved_search, salary_insights, company_insights, interview_prep, market_skill_gap,
         job_search_tables, cv_history, open_dashboard)


def mock_interview(job_id: str, kind: str = "mixed") -> str:
    return (
        f"Run a mock interview for vacancy {job_id} (focus: {kind} — technical, behavioral or mixed).\n\n"
        "1. Call interview_prep(job_id) and get_candidate.\n"
        "2. Ask ONE question at a time, like a real interviewer for this role and level; start with 'tell me about yourself'. "
        "Mix technical topics from the vacancy, behavioral questions and at least one risk question.\n"
        "3. After each answer give short feedback: what was strong, what was missing (structure / specifics / numbers / length), "
        "and a better version built ONLY from the candidate's real facts. Then the next question.\n"
        "4. After 6-8 questions: overall impression, the 3 things to practise, and offer to save new facts the candidate "
        "mentioned with remember_candidate_info."
    )
