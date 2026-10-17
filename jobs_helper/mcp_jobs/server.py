"""MCP server `jobs` (stdio).

Run:      uv run python -m jobs_helper.mcp_jobs.server
Inspect:  npx @modelcontextprotocol/inspector uv run python -m jobs_helper.mcp_jobs.server

Env: AGENT_ROLE, JOBS_SNAPSHOT (path | latest), JOBS_APPLY_BACKEND (mock), JOBS_DRY_RUN, JOBS_WORKSPACE,
     JOBS_CANDIDATE, JOBS_SETTINGS, JOBS_TODAY — see service.py / policy.py.
"""

from __future__ import annotations

import json
import os
import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from jobs_helper.mcp_jobs.service import JobsService, err
from jobs_helper.models import now_iso
from jobs_helper.tracker import workspace

_service: JobsService | None = None
_config_error: str | None = None


def get_service() -> JobsService:
    """Created lazily: a misconfiguration becomes a tool error instead of a crashed server."""
    global _service, _config_error
    if _service is None:
        try:
            _service = JobsService()
        except Exception as e:  # noqa: BLE001
            _config_error = f"{type(e).__name__}: {e}"
            raise
    return _service
mcp = MCPServer(
    name="jobs",
    instructions=(
        "Job boards DOU, Djinni and Happy Monday for one candidate: search vacancies, read details, "
        "submit a tailored application. How an application is actually sent (manual / confirm / auto) "
        "is chosen by the candidate per site and is not controlled by the caller."
    ),
)


def _audit(tool: str, args: dict, result: dict, started: float) -> None:
    short = {k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v) for k, v in args.items()}
    entry = {"ts": now_iso(), "role": os.environ.get("AGENT_ROLE", "manual"), "tool": tool, "args": short, "ok": "error" not in result,
             "error": result.get("error"), "status": result.get("status"),
             "application_id": result.get("application_id"), "ms": round((time.perf_counter() - started) * 1000)}
    try:
        with (workspace() / "mcp_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _safe(tool: str, args: dict, fn) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn(get_service())
    except Exception as e:  # the server must never crash on bad input or a flaky site
        if _config_error:
            result = err("server_misconfigured", _config_error, "The operator must fix JOBS_* settings; do not retry.")
        else:
            result = err("internal_error", f"{type(e).__name__}: {e}", "Retry once; if it fails again, report it.", True)
    _audit(tool, args, result, started)
    return result


@mcp.tool()
def search_jobs(
    keywords: Annotated[str, Field(description="Role and key skills, e.g. 'python django' or 'react typescript'")],
    sources: Annotated[list[Literal["dou", "djinni", "happymonday"]] | None, Field(description="Boards to search; default all three")] = None,
    remote: Annotated[bool | None, Field(description="True: remote vacancies only")] = None,
    city: Annotated[str | None, Field(description="City, e.g. 'Київ' or 'Lviv'")] = None,
    min_salary: Annotated[int | None, Field(description="Monthly USD; drops vacancies whose known salary is lower")] = None,
    max_experience_years: Annotated[float | None, Field(description="Candidate's experience; drops vacancies requiring more")] = None,
    limit: Annotated[int, Field(description="Max results, 1..20")] = 10,
) -> dict[str, Any]:
    """Search OPEN vacancies across DOU, Djinni and Happy Monday. Returns short summaries with job_id;
    fields that a board does not publish (often salary or experience) are null, not zero."""
    args = {"keywords": keywords, "sources": sources, "remote": remote, "city": city, "min_salary": min_salary,
            "max_experience_years": max_experience_years, "limit": limit}
    return _safe("search_jobs", args, lambda svc: svc.search_jobs(**args))


@mcp.tool()
def get_job(
    job_id: Annotated[str, Field(description="Id from search_jobs, e.g. 'dou:acme/376945', 'djinni:853661-python-dev', 'hm:2049225'")],
) -> dict[str, Any]:
    """Full vacancy: description, requirements, experience, salary, work mode, whether it is still open,
    and apply_target — where the application form lives (board itself, an ATS such as ats:greenhouse,
    or something that cannot be filled automatically)."""
    return _safe("get_job", {"job_id": job_id}, lambda svc: svc.get_job(job_id))


@mcp.tool()
def apply(
    job_id: Annotated[str, Field(description="Vacancy id from search_jobs / get_job")],
    cv_markdown: Annotated[str | None, Field(description="CV tailored to this vacancy, markdown, 400-8000 chars; only facts from the candidate's real CV. May be omitted on a retry for the same job_id")] = None,
    cover_letter: Annotated[str | None, Field(description="Cover letter for this vacancy, 150-3000 chars. May be omitted on a retry")] = None,
    answers: Annotated[dict[str, str] | None, Field(description="Answers to questions returned earlier as needs_input: {field_id: value}")] = None,
    attach_original_cv: Annotated[bool, Field(description="Upload the candidate's own CV file (their designed PDF/DOCX) instead of a PDF generated from cv_markdown")] = False,
) -> dict[str, Any]:
    """APPLY to a vacancy (state-changing). Fills the application form with the candidate profile, the CV
    and the cover letter, then sends it according to the candidate's per-site policy, and records it in the
    applications tracker. Returns status prepared | awaiting_user | submitted | declined, or a structured
    error: needs_input (with questions), already_applied, job_closed (with similar_open_jobs),
    external_apply (with url), not_logged_in, captcha_required, forbidden, ..."""
    args = {"job_id": job_id, "cv_markdown": cv_markdown, "cover_letter": cover_letter, "answers": answers,
            "attach_original_cv": attach_original_cv}
    return _safe("apply", args, lambda svc: svc.apply(job_id, cv_markdown, cover_letter, answers, attach_original_cv))


@mcp.resource("jobs://applications", name="applications", description="Applications tracker: every application attempt with its status",
              mime_type="application/json")
def applications_resource() -> str:
    from jobs_helper.tracker import Tracker
    return json.dumps(Tracker().rows(), ensure_ascii=False, indent=1)


@mcp.resource("jobs://policy", name="submit_policy", description="How applications are sent on each site (chosen by the candidate)",
              mime_type="application/json")
def policy_resource() -> str:
    from jobs_helper import policy as pol
    return json.dumps({site: {"policy": value, "note": pol.SITE_NOTES[site]} for site, value in pol.load_policies().items()},
                      ensure_ascii=False, indent=1)


@mcp.prompt(title="Find vacancies and apply", description="Search DOU / Djinni / Happy Monday and apply with a CV tailored truthfully")
def find_and_apply(request: str, max_applications: int = 1) -> str:
    return (
        f"Candidate request: {request}\n\n"
        "0. If the candidate's CV is not in the conversation yet, call get_candidate (or ask them to attach it).\n"
        f"1. Use search_jobs (1-3 focused queries) and open every candidate vacancy with get_job.\n"
        f"2. Pick at most {max_applications} vacancies that really fit the candidate's CV.\n"
        "3. Tailor the CV and write a cover letter using ONLY facts from the candidate's CV — never add skills, "
        "years or employers.\n"
        "4. Call apply. On needs_input answer only from the CV / profile; human_only questions go to the candidate. "
        "On already_applied / job_closed pick another vacancy.\n"
        "5. Report what was submitted, its status and application id."
    )


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
