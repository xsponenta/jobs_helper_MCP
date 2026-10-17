"""Smoke test of the `jobs` MCP server through a real MCP stdio client (no direct imports of the service).

    uv run python scripts/smoke_mcp_jobs.py            # snapshot + mock backend, temp workspace
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml
from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
CV = (ROOT / "scripts" / "sample_cv.md").read_text()
COVER = ("Dear hiring team, I am a Python backend developer with 3.5 years of commercial experience in Django and "
         "FastAPI. I build REST APIs and background processing with Celery and PostgreSQL, and I would be glad to "
         "bring this experience to your team.")


def show(title: str, res) -> dict:
    text = res.content[0].text if res.content else ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:  # protocol-level validation errors come back as plain text
        data = {"error": "validation", "message": text}
    status = data.get("error") or data.get("status") or f"{data.get('count', '')} jobs"
    print(f"\n> {title}  [isError={res.is_error}]  -> {status}")
    print("  " + json.dumps(data, ensure_ascii=False)[:420])
    return data


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="jobs_smoke_"))
    (tmp / "candidate.yaml").write_text(yaml.safe_dump({
        "personal": {"first_name": "Olena", "last_name": "Kovalenko", "email": "olena@example.com",
                     "phone": "+380000000000", "city": "Lviv", "country": "Ukraine"},
        "links": {"linkedin": "https://linkedin.com/in/olena-example"},
        "job_preferences": {"desired_salary_usd": 3500, "notice_period": "2 weeks"},
        "languages": {"english": "B2"},
    }, allow_unicode=True))
    env = {**os.environ, "AGENT_ROLE": "reviewer", "JOBS_SNAPSHOT": str(ROOT / "data/snapshots/2026-10-17_eval.json"),
           "JOBS_WORKSPACE": str(tmp / "ws"), "JOBS_CANDIDATE": str(tmp / "candidate.yaml"),
           "JOBS_SETTINGS": str(tmp / "settings.yaml"), "JOBS_TODAY": "2026-10-17", "JOBS_APPLY_BACKEND": "mock"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "jobs_helper.mcp_jobs.server"], env=env, cwd=str(ROOT))

    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = await s.list_tools()
        print("TOOLS:", [t.name for t in tools.tools])
        for t in tools.tools:
            print(f"  {t.name}: params={list(t.input_schema.get('properties', {}))}")

        found = show("search_jobs('python', remote)", await s.call_tool("search_jobs", {"keywords": "python", "remote": True, "limit": 5}))
        show("search_jobs('') — bad input", await s.call_tool("search_jobs", {"keywords": ""}))
        jid = found["jobs"][0]["job_id"]
        job = show(f"get_job({jid})", await s.call_tool("get_job", {"job_id": jid}))
        show("get_job('garbage')", await s.call_tool("get_job", {"job_id": "garbage"}))

        snap = json.loads(Path(env["JOBS_SNAPSHOT"]).read_text())["jobs"]
        by_target = {}
        for j in snap:
            t = (j["source"] if j["apply_type"] != "external" else
                 ("greenhouse" if "greenhouse" in (j.get("apply_url") or "") else "other_ext"))
            by_target.setdefault(t, j["id"])

        show("apply(djinni) — policy confirm", await s.call_tool("apply", {"job_id": by_target["djinni"], "cv_markdown": CV, "cover_letter": COVER}))
        show("apply(djinni) again", await s.call_tool("apply", {"job_id": by_target["djinni"], "cv_markdown": CV, "cover_letter": COVER}))
        show("apply(dou) — policy manual", await s.call_tool("apply", {"job_id": by_target["dou"], "cv_markdown": CV, "cover_letter": COVER}))
        gh = show("apply(greenhouse) — custom questions", await s.call_tool("apply", {"job_id": by_target["greenhouse"], "cv_markdown": CV, "cover_letter": COVER}))
        show("apply(greenhouse) with answers", await s.call_tool("apply", {"job_id": by_target["greenhouse"], "cv_markdown": CV, "cover_letter": COVER, "answers": {"q_rate": "22"}}))
        show("apply — cv too short", await s.call_tool("apply", {"job_id": by_target["djinni"], "cv_markdown": "hi", "cover_letter": COVER}))
        show("apply — wrong argument type", await s.call_tool("apply", {"job_id": 42}))

    print("\nTRACKER:\n" + (tmp / "ws" / "applications.csv").read_text())
    print("AUDIT LOG (last 3):")
    for line in (tmp / "ws" / "mcp_calls.jsonl").read_text().split("\n")[-4:-1]:
        print("  " + line)


asyncio.run(main())
