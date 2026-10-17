"""Runs the 5 test requests through the two-agent system and the single-agent baseline and grades them.

  uv run python -m evaluation.run_eval                       # all cases, both modes
  uv run python -m evaluation.run_eval --cases c1,c3 --modes multi --repeat 2

Output: runs/eval_<timestamp>/  (per-run trace.jsonl, workspace, result.json) + results.json + report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

import yaml

from evaluation.graders import GRADERS, RunOutcome
from jobs_helper import config
from jobs_helper.agents.orchestrator import run_request
from jobs_helper.agents.tracing import load_trace
from jobs_helper.models import Job
from jobs_helper.tracker import Tracker

HERE = Path(__file__).resolve().parent


def seed_workspace(ws: Path, candidate_dir: Path, seed: list[dict]) -> set[str]:
    ws.mkdir(parents=True, exist_ok=True)
    if not seed:
        return set()
    profile = yaml.safe_load((candidate_dir / "candidate.yaml").read_text(encoding="utf-8"))
    name = f"{profile['personal']['first_name']} {profile['personal']['last_name']}"
    tr, ids = Tracker(ws), set()
    for s in seed:
        app_id = tr.next_id()
        tr.add(application_id=app_id, candidate=name, job_id=s["job_id"], status=s.get("status", "submitted"),
               policy="manual", submitted_by="seed")
        ids.add(app_id)
    return ids


def metrics(run_dir: Path) -> dict:
    trace = load_trace(run_dir / "trace.jsonl")
    ends = [e for e in trace if e["kind"] == "agent_end"]
    tools = [e for e in trace if e["kind"] == "tool_result"]
    return {
        "seconds": trace[-1]["t"] if trace else None,
        "agent_runs": len(ends),
        "turns": sum(e.get("num_turns") or 0 for e in ends),
        "tool_calls": len(tools),
        "tool_errors": sorted({e["error"] for e in tools if e.get("error")}),
        "cost_usd": round(sum(e.get("cost_usd") or 0 for e in ends), 4),
        "output_tokens": sum(((e.get("usage") or {}).get("output_tokens") or 0) for e in ends),
    }


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", help="comma separated ids; default all")
    p.add_argument("--modes", default="multi,single")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--model")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--resume", help="existing runs/eval_* dir: reuse finished runs, run only the missing ones")
    args = p.parse_args()

    spec = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    snapshot = str((config.ROOT / spec["snapshot"]).resolve())
    jobs = {j["id"]: Job(**j) for j in json.loads(Path(snapshot).read_text(encoding="utf-8"))["jobs"]}
    cases = [c for c in spec["cases"] if not args.cases or c["id"] in args.cases.split(",")]
    out_dir = Path(args.resume) if args.resume else config.ROOT / "runs" / f"eval_{datetime.now():%Y%m%d_%H%M%S}"
    results = []

    for case in cases:
        for mode in args.modes.split(","):
            for rep in range(1, args.repeat + 1):
                name = f"{case['id']}__{mode}" + (f"__r{rep}" if args.repeat > 1 else "")
                cand = config.ROOT / "data" / "candidates" / case["candidate"]
                ws = out_dir / name / "workspace"
                done = out_dir / name / "result.json"
                print(f"\n=== {name}", flush=True)
                try:
                    if args.resume and done.exists():
                        res = json.loads(done.read_text(encoding="utf-8"))
                        seeded = {r["application_id"] for r in Tracker(ws).rows() if r["submitted_by"] == "seed"}
                    else:
                        seeded = seed_workspace(ws, cand, case.get("seed", []))
                        res = await run_request(case["request"], cand, mode, runs_root=out_dir, run_name=name,
                                                snapshot=snapshot, dry_run=True, model=args.model,
                                                verbose=not args.quiet, workspace=ws, today=spec["today"])
                    outcome = RunOutcome(ws, res, jobs, seeded, spec["not_skills"].get(case["candidate"], []))
                    grade = GRADERS[case["grader"]](outcome, case.get("params", {}))
                except Exception as e:  # a crashed run is a failed run, not a crashed evaluation
                    res, grade = {"final_answer": ""}, None
                    grade_d = {"passed": False, "reason": f"run crashed: {type(e).__name__}: {e}", "details": {}}
                else:
                    grade_d = {"passed": grade.passed, "reason": grade.reason, "details": grade.details}
                try:
                    m = metrics(out_dir / name)
                except (OSError, ValueError) as e:
                    m = {"metrics_error": str(e)}
                row = {"case": case["id"], "mode": mode, "rep": rep, **grade_d, **m,
                       "final_answer": res.get("final_answer", "")}
                results.append(row)
                print(f"--> {'PASS' if row['passed'] else 'FAIL'}: {row['reason']}", flush=True)
                (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    (out_dir / "report.md").write_text(render_report(results, cases), encoding="utf-8")
    print(f"\nReport: {out_dir / 'report.md'}")


def render_report(results: list[dict], cases: list[dict]) -> str:
    modes = sorted({r["mode"] for r in results}, key=["multi", "single"].index)
    lines = ["# Результати оцінки", "", "| Кейс | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    for c in cases:
        cells = []
        for m in modes:
            rs = [r for r in results if r["case"] == c["id"] and r["mode"] == m]
            ok = sum(r["passed"] for r in rs)
            cells.append(f"{'✅' if ok == len(rs) else ('❌' if ok == 0 else '⚠️')} {ok}/{len(rs)}")
        lines.append(f"| {c['id']} | " + " | ".join(cells) + " |")
    lines += ["", "| Режим | Пройдено | Сер. час, с | Сер. викликів інструментів | Сер. ходів | Вартість, $ (еквівалент API) |",
              "|---|---|---|---|---|---|"]
    for m in modes:
        rs = [r for r in results if r["mode"] == m]
        avg = lambda k: round(sum(r.get(k) or 0 for r in rs) / max(len(rs), 1), 1)  # noqa: E731
        lines.append(f"| {m} | {sum(r['passed'] for r in rs)}/{len(rs)} | {avg('seconds')} | {avg('tool_calls')} | "
                     f"{avg('turns')} | {round(sum(r.get('cost_usd') or 0 for r in rs), 3)} |")
    lines += ["", "## Деталі", ""]
    for r in results:
        lines.append(f"- **{r['case']} / {r['mode']}{' r' + str(r['rep']) if r['rep'] > 1 else ''}** — "
                     f"{'PASS' if r['passed'] else 'FAIL'}: {r['reason']} "
                     f"(помилки інструментів: {', '.join(r.get('tool_errors') or []) or '—'})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    asyncio.run(main())
