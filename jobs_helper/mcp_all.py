"""All-in-one MCP server `jobs-helper` for Claude Desktop / Claude Code users.

Same tools as the `jobs` and `linkedin` servers plus get_candidate (the candidate's profile and CV text), so a
single Claude can do the whole flow. The coursework's two-agent system keeps using the separate servers.

Run:  jobs-helper-mcp            (installed)   |   uv run python -m jobs_helper.mcp_all   (checkout)
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from jobs_helper import config
from jobs_helper import policy as pol
from jobs_helper.mcp_jobs import server as jobs_server
from jobs_helper.mcp_linkedin import server as linkedin_server

INSTRUCTIONS = """Job search and application assistant for ONE candidate (Ukrainian IT boards DOU, Djinni, Happy Monday,
plus company career sites / ATS, plus the candidate's LinkedIn).

Workflow: get_candidate first (profile + CV text) -> search_jobs -> get_job for each shortlisted vacancy ->
tailor the CV and write a cover letter -> apply. For LinkedIn: publish_post, save_profile_update.

Rules:
- Truthfulness: use only facts from the candidate's CV/profile. Never add skills, employers, years, metrics or
  certificates — even if asked. Missing requirements are mentioned honestly, never written into the CV.
- apply decides HOW the application is sent from the candidate's own per-site policy (manual / confirm / auto);
  a browser window may open for the candidate to confirm, log in or press Submit. Tell the candidate what to expect.
- On needs_input answer only from the CV/profile; questions marked human_only (consent, visa, work permit) must be
  asked to the candidate. Never invent answers.
- Do not mass-apply: apply only to vacancies the candidate asked for or approved."""

mcp = MCPServer(name="jobs-helper", instructions=INSTRUCTIONS)


@mcp.tool()
def get_candidate() -> dict[str, Any]:
    """The candidate's profile (contacts, links, preferences, languages) and the full text of their CV.
    Sensitive answers (visa, work permit) are not returned; the server uses them only inside forms."""
    try:
        profile = pol.load_candidate()
        if not profile:
            return {"error": "candidate_profile_missing", "message": "No candidate profile is configured.",
                    "hint": "Claude Desktop: Settings → Extensions → Jobs Helper → fill the form. "
                            "Command line: `jobs-helper init`.", "retryable": False}
        public = {k: v for k, v in profile.items() if k not in ("sensitive", "cv_file")}
        path = pol.cv_file(profile)
        out: dict[str, Any] = {"profile": public, "cv_file": str(path) if path else None, "data_dir": str(config.HOME)}
        if path:
            out["cv_text"] = pol.read_cv_text(path)[:30000]
        else:
            out["hint"] = "No CV file found: ask the candidate to attach the CV in the chat or set it in the extension settings."
        return out
    except Exception as e:  # noqa: BLE001 — never crash the server
        return {"error": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "Check the CV file.", "retryable": False}


for fn in (jobs_server.search_jobs, jobs_server.get_job, jobs_server.apply,
           linkedin_server.get_profile, linkedin_server.publish_post, linkedin_server.save_profile_update):
    mcp.tool()(fn)

mcp.resource("jobs://applications", name="applications", description="Applications tracker",
             mime_type="application/json")(jobs_server.applications_resource)
mcp.resource("jobs://policy", name="submit_policy", description="How applications are sent on each site",
             mime_type="application/json")(jobs_server.policy_resource)
mcp.prompt(title="Find vacancies and apply",
           description="Search DOU / Djinni / Happy Monday and apply with a truthfully tailored CV")(jobs_server.find_and_apply)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
