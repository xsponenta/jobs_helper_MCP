"""MCP server `linkedin` (stdio): the candidate's LinkedIn — profile, posts, profile update text.

Run:  uv run python -m jobs_helper.mcp_linkedin.server
Env:  AGENT_ROLE, JOBS_DRY_RUN, JOBS_WORKSPACE, JOBS_SETTINGS, JOBS_SECRETS_DIR, LINKEDIN_VERSION
"""

from __future__ import annotations

import json
import os
import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from jobs_helper.linkedin.service import LinkedInService, err
from jobs_helper.models import now_iso
from jobs_helper.tracker import workspace

service = LinkedInService()
mcp = MCPServer(
    name="linkedin",
    instructions=(
        "The candidate's LinkedIn via the official API: read the basic profile, publish a post (how it is "
        "published — manual / confirm / auto — is the candidate's choice), and save ready-to-paste profile text. "
        "LinkedIn offers no API to edit a profile."
    ),
)


def _safe(tool: str, args: dict, fn) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as e:  # never crash the server
        result = err("internal_error", f"{type(e).__name__}: {e}", "Retry once; if it fails again, report it.", True)
    entry = {"ts": now_iso(), "role": os.environ.get("AGENT_ROLE", "manual"), "server": "linkedin", "tool": tool,
             "args": {k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v) for k, v in args.items()},
             "ok": "error" not in result, "error": result.get("error"), "status": result.get("status"),
             "ms": round((time.perf_counter() - started) * 1000)}
    try:
        with (workspace() / "mcp_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return result


@mcp.tool()
def get_profile() -> dict[str, Any]:
    """The candidate's basic LinkedIn profile (name, email, photo). Errors: not_connected, token_expired."""
    return _safe("get_profile", {}, service.get_profile)


@mcp.tool()
def publish_post(
    text: Annotated[str, Field(description="Post text, up to 3000 chars; only facts from the candidate's CV")],
    visibility: Annotated[Literal["PUBLIC", "CONNECTIONS"], Field(description="Who sees the post")] = "PUBLIC",
) -> dict[str, Any]:
    """PUBLISH a post on the candidate's LinkedIn (state-changing), according to the candidate's policy.
    Returns status prepared (dry run) | awaiting_user | declined | published, or an error:
    not_connected, token_expired, post_too_long, rate_limited, forbidden."""
    return _safe("publish_post", {"text": text, "visibility": visibility}, lambda: service.publish_post(text, visibility))


@mcp.tool()
def save_profile_update(
    headline: Annotated[str, Field(description="New LinkedIn headline, up to 220 chars")],
    about: Annotated[str, Field(description="New 'About' section, up to 2600 chars")],
    skills: Annotated[list[str], Field(description="Skills to list, only ones present in the CV")],
    experience_bullets: Annotated[list[str] | None, Field(description="Optional improved experience bullet points")] = None,
    positions: Annotated[list[dict[str, str]] | None, Field(description="Per-position texts: [{title, company, dates, description}] (description ≤ 2000 chars)")] = None,
    approved_by_candidate: Annotated[bool, Field(description="Set true only after the candidate approved the draft: opens a copy-helper next to their profile")] = False,
) -> dict[str, Any]:
    """PREPARE a LinkedIn profile update from the CV (state-changing: saves files). First call returns a draft to show the
    candidate; after their approval call again with approved_by_candidate=true — a page with copy buttons opens next to
    their LinkedIn profile and they paste each block. LinkedIn offers no API to edit profiles and forbids automating it."""
    args = {"headline": headline, "about": about, "skills": skills, "experience_bullets": experience_bullets,
            "positions": positions, "approved_by_candidate": approved_by_candidate}
    return _safe("save_profile_update", args, lambda: service.save_profile_update(headline, about, skills, experience_bullets,
                                                                                  positions, approved_by_candidate))


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
