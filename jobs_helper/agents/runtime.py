"""Runs one agent on the Claude Agent SDK with exactly the MCP tools it is allowed to use.

Isolation: no built-in Claude Code tools (tools=[]), no user/project settings or CLAUDE.md
(setting_sources=[]), no other MCP servers (strict_mcp_config). The `jobs` MCP server is started
as a subprocess per agent with that agent's AGENT_ROLE, so the server can enforce write access too.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage, SystemMessage,
                              TextBlock, ToolUseBlock, query)

from jobs_helper import config
from jobs_helper.agents.tracing import Tracer

SERVERS = {
    "jobs": {"module": "jobs_helper.mcp_jobs.server", "tools": ("search_jobs", "get_job", "apply")},
    "linkedin": {"module": "jobs_helper.mcp_linkedin.server", "tools": ("get_profile", "publish_post", "save_profile_update")},
}


def tool_id(name: str) -> str:
    """'search_jobs' (jobs server) or 'linkedin.publish_post' -> MCP tool id as the SDK names it."""
    server, _, tool = name.rpartition(".")
    return f"mcp__{server or 'jobs'}__{tool}"


def display_name(sdk_name: str) -> str:
    """mcp__jobs__apply -> apply, mcp__linkedin__publish_post -> linkedin.publish_post"""
    _, server, tool = sdk_name.split("__", 2) if sdk_name.startswith("mcp__") else ("", "", sdk_name)
    return tool if server in ("jobs", "") else f"{server}.{tool}"


@dataclass
class AgentSpec:
    name: str                       # scout | reviewer | single_agent (also used as AGENT_ROLE)
    system_prompt: str
    tools: list[str]                # 'search_jobs', 'apply', 'linkedin.publish_post', ...
    output_schema: dict | None = None
    max_turns: int = 20


@dataclass
class RunContext:
    run_dir: Path
    tracer: Tracer
    server_env: dict[str, str]      # JOBS_* variables for the MCP server
    model: str | None = None


@dataclass
class AgentResult:
    text: str = ""
    structured: Any = None
    is_error: bool = False
    subtype: str = ""
    num_turns: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: float | None = None
    duration_ms: int = 0
    usage: dict | None = None
    mcp_failed: bool = False


def _parse_tool_response(resp: Any) -> Any:
    """MCP tool responses arrive as content blocks; return the JSON payload if there is one."""
    if isinstance(resp, dict) and "content" in resp:
        resp = resp["content"]
    if isinstance(resp, list):
        texts = [b.get("text", "") for b in resp if isinstance(b, dict) and b.get("type") == "text"]
        resp = "\n".join(texts) if texts else resp
    if isinstance(resp, str):
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            return resp
    return resp


async def run_agent(spec: AgentSpec, prompt: str, ctx: RunContext, round_no: int = 1) -> AgentResult:
    tracer = ctx.tracer
    result = AgentResult()

    async def pre_tool(input_data, tool_use_id, _context):
        tracer.event("tool_call", spec.name, tool=display_name(input_data["tool_name"]),
                     input=input_data.get("tool_input"), tool_use_id=tool_use_id)
        return {}

    async def post_tool(input_data, tool_use_id, _context):
        out = _parse_tool_response(input_data.get("tool_response"))
        name = display_name(input_data["tool_name"])
        error = out.get("error") if isinstance(out, dict) else None
        result.tool_calls.append({"tool": name, "input": input_data.get("tool_input"), "output": out})
        tracer.event("tool_result", spec.name, tool=name, error=error, output=out, tool_use_id=tool_use_id,
                     duration_ms=input_data.get("duration_ms"))
        return {}

    async def post_tool_failure(input_data, tool_use_id, _context):
        name = display_name(input_data["tool_name"])
        result.tool_calls.append({"tool": name, "input": input_data.get("tool_input"), "output": {"error": "tool_failure"}})
        tracer.event("tool_result", spec.name, tool=name, error="tool_failure",
                     output={"message": input_data.get("error")}, tool_use_id=tool_use_id)
        return {}

    env = {**ctx.server_env, "AGENT_ROLE": spec.name, "PYTHONPATH": str(config.ROOT),
           "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    used = {(t.rpartition(".")[0] or "jobs") for t in spec.tools}
    allowed = {tool_id(t) for t in spec.tools}
    options = ClaudeAgentOptions(
        system_prompt=spec.system_prompt,
        mcp_servers={name: {"type": "stdio", "command": sys.executable, "args": ["-m", SERVERS[name]["module"]], "env": env}
                     for name in sorted(used)},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=sorted(allowed),
        disallowed_tools=sorted(f"mcp__{n}__{t}" for n in used for t in SERVERS[n]["tools"] if f"mcp__{n}__{t}" not in allowed),
        permission_mode="dontAsk",
        setting_sources=[],
        max_turns=spec.max_turns,
        model=ctx.model,
        cwd=str(ctx.run_dir),
        output_format={"type": "json_schema", "schema": spec.output_schema} if spec.output_schema else None,
        hooks={
            "PreToolUse": [HookMatcher(matcher="mcp__.*", hooks=[pre_tool])],
            "PostToolUse": [HookMatcher(matcher="mcp__.*", hooks=[post_tool])],
            "PostToolUseFailure": [HookMatcher(matcher="mcp__.*", hooks=[post_tool_failure])],
        },
    )

    tracer.event("agent_start", spec.name, round=round_no, tools=spec.tools, prompt_chars=len(prompt))
    texts: list[str] = []
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            visible = [t for t in msg.data.get("tools", []) if t.startswith("mcp__")]
            statuses = [s.get("status") for s in msg.data.get("mcp_servers", [])]
            tracer.event("agent_init", spec.name, model=msg.data.get("model"), visible_tools=visible, mcp=statuses)
            if not statuses or any(st != "connected" for st in statuses):
                result.mcp_failed = True
                tracer.event("error", spec.name, message="MCP server 'jobs' did not connect", mcp=statuses)
                break
        elif isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    texts.append(b.text)
                    tracer.event("assistant_text", spec.name, text=b.text)
                elif isinstance(b, ToolUseBlock):
                    pass  # traced by hooks
        elif isinstance(msg, ResultMessage):
            result.is_error, result.subtype = msg.is_error, msg.subtype
            result.num_turns, result.cost_usd = msg.num_turns, msg.total_cost_usd
            result.duration_ms, result.usage = msg.duration_ms, msg.usage
            result.structured = msg.structured_output
            result.text = msg.result or "\n".join(texts)
    if result.mcp_failed:
        return result
    tracer.event("agent_end", spec.name, round=round_no, num_turns=result.num_turns, tool_calls=len(result.tool_calls),
                 duration_ms=result.duration_ms, cost_usd=result.cost_usd, is_error=result.is_error,
                 subtype=result.subtype, usage=result.usage, structured=result.structured)
    return result
