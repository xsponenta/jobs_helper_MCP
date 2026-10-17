"""Two ways to run a request:
- multi:  Scout (search_jobs, get_job)  ->  Reviewer (get_job, apply), at most one revision round;
- single: one agent with all three tools (the baseline for the comparison).

Each run gets its own directory with trace.jsonl, result.json and the workspace of the MCP server.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from jobs_helper import config
from jobs_helper.agents.prompts import (REVIEWER_PROMPT, REVIEWER_SCHEMA, SCOUT_PROMPT, SCOUT_SCHEMA, SINGLE_PROMPT,
                                        SINGLE_SCHEMA)
from jobs_helper.agents.runtime import AgentResult, AgentSpec, RunContext, run_agent
from jobs_helper.agents.tracing import Tracer

SCOUT = AgentSpec("scout", SCOUT_PROMPT, ["search_jobs", "get_job", "linkedin.get_profile"], SCOUT_SCHEMA, max_turns=25)
REVIEWER = AgentSpec("reviewer", REVIEWER_PROMPT, ["get_job", "apply", "linkedin.publish_post", "linkedin.save_profile_update"],
                     REVIEWER_SCHEMA, max_turns=25)
SINGLE = AgentSpec("single_agent", SINGLE_PROMPT, ["search_jobs", "get_job", "apply", "linkedin.get_profile",
                                                   "linkedin.publish_post", "linkedin.save_profile_update"], SINGLE_SCHEMA, max_turns=35)
MAX_ROUNDS = 2


def candidate_block(candidate_dir: Path) -> tuple[str, dict]:
    cv = (candidate_dir / "cv.md").read_text()
    profile = yaml.safe_load((candidate_dir / "candidate.yaml").read_text()) or {}
    public = {k: v for k, v in profile.items() if k != "sensitive"}   # sensitive answers stay with the server
    text = f"## Candidate CV (the only source of facts)\n\n{cv}\n\n## Candidate profile\n\n```yaml\n{yaml.safe_dump(public, allow_unicode=True)}```"
    return text, profile


def make_context(run_dir: Path, run_id: str, candidate_dir: Path, *, snapshot: str | None, dry_run: bool,
                 model: str | None, verbose: bool, workspace: Path | None = None, today: str | None = None) -> RunContext:
    candidate_dir, run_dir = candidate_dir.resolve(), run_dir.resolve()
    if snapshot and snapshot != "latest":
        snapshot = str(Path(snapshot).resolve())
    env = {"JOBS_WORKSPACE": str((workspace or run_dir / "workspace").resolve()),
           "JOBS_CANDIDATE": str(candidate_dir / "candidate.yaml"),
           "JOBS_SETTINGS": os.environ.get("JOBS_SETTINGS", str(config.ROOT / "settings.yaml")),
           "JOBS_APPLY_BACKEND": os.environ.get("JOBS_APPLY_BACKEND", "mock")}
    if snapshot:
        env["JOBS_SNAPSHOT"] = snapshot
    if dry_run:
        env["JOBS_DRY_RUN"] = "1"
    if today:
        env["JOBS_TODAY"] = today
    for k in ("JOBS_MOCK_CONFIRM", "JOBS_MOCK_SESSIONS", "JOBS_MOCK_CAPTCHA", "JOBS_SECRETS_DIR", "JOBS_HEADLESS",
              "JOBS_HUMAN_TIMEOUT"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return RunContext(run_dir=run_dir, tracer=Tracer(run_dir, run_id, verbose), server_env=env, model=model)


async def run_multi(request: str, candidate_dir: Path, ctx: RunContext) -> dict:
    cand_text, _ = candidate_block(candidate_dir)
    feedback, scout_res, reviewer_res = "", None, None
    rounds = []
    for round_no in range(1, MAX_ROUNDS + 1):
        scout_prompt = f"{cand_text}\n\n## User request\n\n{request}"
        if feedback:
            scout_prompt += f"\n\n## Reviewer feedback on your previous drafts\n\n{feedback}"
        scout_res = await run_agent(SCOUT, scout_prompt, ctx, round_no)
        if scout_res.mcp_failed:
            return _mcp_failure(scout_res)
        drafts = scout_res.structured or {"drafts": [], "note": "scout returned no structured output", "raw": scout_res.text}
        ctx.tracer.event("handoff", "orchestrator", src="scout", dst="reviewer", round=round_no,
                         summary=f"{len(drafts.get('drafts', []))} drafts, submit_requested={drafts.get('submit_requested')}",
                         drafts=[d.get("job_id") for d in drafts.get("drafts", [])])
        reviewer_prompt = (f"## User request\n\n{request}\n\n{cand_text}\n\n"
                           f"## Scout drafts (round {round_no})\n\n```json\n{json.dumps(drafts, ensure_ascii=False, indent=1)}\n```")
        reviewer_res = await run_agent(REVIEWER, reviewer_prompt, ctx, round_no)
        if reviewer_res.mcp_failed:
            return _mcp_failure(reviewer_res)
        rounds.append({"scout": _summary(scout_res), "reviewer": _summary(reviewer_res)})
        verdict = reviewer_res.structured or {}
        if not verdict.get("needs_revision") or round_no == MAX_ROUNDS:
            break
        feedback = verdict.get("feedback_for_scout", "")
        ctx.tracer.event("handoff", "orchestrator", src="reviewer", dst="scout", round=round_no, summary=feedback)
    verdict = (reviewer_res.structured if reviewer_res else None) or {}
    return {
        "final_answer": verdict.get("final_answer") or (reviewer_res.text if reviewer_res else ""),
        "questions_for_candidate": verdict.get("questions_for_candidate", []),
        "decisions": verdict.get("decisions", []),
        "scout_output": scout_res.structured if scout_res else None,
        "rounds": rounds,
    }


async def run_single(request: str, candidate_dir: Path, ctx: RunContext) -> dict:
    cand_text, _ = candidate_block(candidate_dir)
    res = await run_agent(SINGLE, f"{cand_text}\n\n## User request\n\n{request}", ctx)
    if res.mcp_failed:
        return _mcp_failure(res)
    out = res.structured or {}
    return {"final_answer": out.get("final_answer") or res.text,
            "questions_for_candidate": out.get("questions_for_candidate", []),
            "rounds": [{"single_agent": _summary(res)}]}


async def run_request(request: str, candidate_dir: Path, mode: str = "multi", *, runs_root: Path | None = None,
                      snapshot: str | None = None, dry_run: bool = False, model: str | None = None,
                      verbose: bool = True, workspace: Path | None = None, today: str | None = None,
                      run_name: str | None = None) -> dict:
    run_id = uuid.uuid4().hex[:8]
    run_dir = (runs_root or config.ROOT / "runs") / (run_name or f"{datetime.now():%Y%m%d_%H%M%S}_{mode}_{run_id}")
    ctx = make_context(run_dir, run_id, candidate_dir, snapshot=snapshot, dry_run=dry_run, model=model,
                       verbose=verbose, workspace=workspace, today=today)
    ctx.tracer.event("run_start", None, mode=mode, candidate=candidate_dir.name, request=request,
                     snapshot=snapshot, dry_run=dry_run)
    result = await (run_multi if mode == "multi" else run_single)(request, candidate_dir, ctx)
    result |= {"mode": mode, "run_dir": str(run_dir), "workspace": ctx.server_env["JOBS_WORKSPACE"]}
    ctx.tracer.event("run_end", None, final_answer=result["final_answer"])
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return result


def _mcp_failure(r: AgentResult) -> dict:
    return {"final_answer": "Сервер вакансій (MCP `jobs`) не запустився; нічого не виконано. Перевір шляхи у змінних JOBS_* "
                            "і запусти `uv run python scripts/smoke_mcp_jobs.py`.",
            "questions_for_candidate": [], "error": "mcp_failed", "rounds": [{"failed": _summary(r)}]}


def _summary(r: AgentResult | None) -> dict:
    if r is None:
        return {}
    d = asdict(r)
    d.pop("text", None)
    d["tool_calls"] = [{"tool": c["tool"], "error": (c["output"] or {}).get("error") if isinstance(c["output"], dict) else None,
                        "status": (c["output"] or {}).get("status") if isinstance(c["output"], dict) else None}
                       for c in r.tool_calls]
    return d
