"""Tracing for agent runs: every event goes to <run_dir>/trace.jsonl and to a colored console log.

Span structure: run -> agent (round) -> tool call. Events:
run_start, agent_start, tool_call, tool_result, assistant_text, agent_end, handoff, run_end.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from jobs_helper.models import now_iso

COLORS = {"scout": "\033[36m", "reviewer": "\033[35m", "single_agent": "\033[33m", "orchestrator": "\033[34m"}
RESET, DIM, BOLD, RED, GREEN, YELLOW = "\033[0m", "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[33m"


def _short(value: Any, n: int = 160) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Tracer:
    def __init__(self, run_dir: Path, run_id: str, verbose: bool = True):
        self.run_dir, self.run_id, self.verbose = run_dir, run_id, verbose
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "trace.jsonl"
        self.t0 = time.perf_counter()

    def event(self, kind: str, agent: str | None = None, **data: Any) -> None:
        rec = {"ts": now_iso(), "t": round(time.perf_counter() - self.t0, 2), "run_id": self.run_id,
               "kind": kind, "agent": agent, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if self.verbose:
            print(self._format(rec), file=sys.stderr, flush=True)

    def _format(self, r: dict) -> str:
        c = COLORS.get(r.get("agent") or "", BOLD)
        tag = f"{c}{BOLD}[{r['agent'] or 'run'}]{RESET}"
        k = r["kind"]
        if k == "run_start":
            line = f"{BOLD}▶ {r.get('mode')} run{RESET}  candidate={r.get('candidate')}  “{_short(r.get('request'), 110)}”"
        elif k == "agent_start":
            line = f"{c}▶ start{RESET} round {r.get('round', 1)}  tools={r.get('tools')}"
        elif k == "tool_call":
            line = f"→ {BOLD}{r['tool']}{RESET}({_short(r.get('input'), 130)})"
        elif k == "tool_result":
            out = r.get("output") or {}
            if r.get("error"):
                line = f"{RED}✗ {r['tool']} → {r['error']}{RESET}  {_short(out.get('message') if isinstance(out, dict) else out, 150)}"
            elif isinstance(out, dict) and out.get("status"):
                line = f"{GREEN}✓ {r['tool']} → {out['status']}{RESET} {out.get('application_id', '')} {out.get('job_id', '')}"
            else:
                line = f"{GREEN}✓ {r['tool']}{RESET} {DIM}{_short(out, 140)}{RESET}"
        elif k == "assistant_text":
            line = f"{DIM}💬 {_short(r.get('text'), 180)}{RESET}"
        elif k == "agent_end":
            err = f" {RED}{r.get('subtype')}{RESET}" if r.get("is_error") else ""
            line = (f"{c}■ end{RESET} turns={r.get('num_turns')} tool_calls={r.get('tool_calls')} "
                    f"{(r.get('duration_ms') or 0) / 1000:.1f}s{err}")
        elif k == "handoff":
            line = f"{YELLOW}⇢ {r.get('src')} → {r.get('dst')}{RESET}  {_short(r.get('summary'), 150)}"
        elif k == "run_end":
            line = f"{BOLD}■ run finished{RESET} in {r['t']:.0f}s"
        else:
            line = f"{k} {_short({x: y for x, y in r.items() if x not in ('ts', 't', 'run_id', 'kind', 'agent')})}"
        return f"{DIM}{r['t']:7.1f}s{RESET} {tag} {line}"


def load_trace(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").split("\n") if x.strip()]
