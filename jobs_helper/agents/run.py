"""Run the assistant on one request.

  uv run python -m jobs_helper.agents.run --candidate data/candidates/olena "Знайди remote Python вакансію і відгукнись"
  uv run python -m jobs_helper.agents.run --mode single --snapshot latest --dry-run --candidate ... "..."
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from jobs_helper.agents.orchestrator import run_request


def main() -> None:
    p = argparse.ArgumentParser(prog="jobs_helper.agents.run")
    p.add_argument("request")
    p.add_argument("--candidate", required=True, type=Path, help="directory with cv.md and candidate.yaml")
    p.add_argument("--mode", choices=["multi", "single"], default="multi")
    p.add_argument("--snapshot", help="snapshot path or 'latest'; default: live sites")
    p.add_argument("--dry-run", action="store_true", help="fill forms but send nothing")
    p.add_argument("--model", help="Claude model alias, e.g. sonnet / opus; default: Claude Code default")
    p.add_argument("--today", help="fixed date YYYY-MM-DD (for snapshots)")
    args = p.parse_args()
    result = asyncio.run(run_request(args.request, args.candidate, args.mode, snapshot=args.snapshot,
                                     dry_run=args.dry_run, model=args.model, today=args.today))
    print("\n" + "=" * 80 + "\n" + result["final_answer"])
    if result.get("questions_for_candidate"):
        print("\nПитання до кандидата:\n- " + "\n- ".join(result["questions_for_candidate"]))
    print(f"\nТрейс: {result['run_dir']}/trace.jsonl")


if __name__ == "__main__":
    main()
