"""Renders a run's trace.jsonl as a self-contained HTML timeline (for the demo / slides).

    uv run python scripts/trace_view.py runs/<run>/trace.jsonl      # writes trace.html next to it
"""

import html
import json
import sys
from pathlib import Path

COLORS = {"scout": "#0e7490", "reviewer": "#9d174d", "single_agent": "#a16207", "orchestrator": "#1d4ed8", None: "#334155"}


def short(v, n=220):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return html.escape(s if len(s) <= n else s[: n - 1] + "…")


def row(e: dict) -> str | None:
    k, agent = e["kind"], e.get("agent")
    color = COLORS.get(agent, "#334155")
    badge = f'<span class="badge" style="background:{color}">{html.escape(agent or "run")}</span>'
    if k == "run_start":
        body = f"<b>{e.get('mode')} run</b> · candidate <b>{html.escape(e.get('candidate', ''))}</b><br>{short(e.get('request'), 400)}"
    elif k == "agent_start":
        body = f"<b>start</b> round {e.get('round')} · tools: {', '.join(e.get('tools', []))}"
    elif k == "agent_init":
        body = f"model {e.get('model')} · visible tools: {', '.join(t.removeprefix('mcp__jobs__') for t in e.get('visible_tools', []))}"
    elif k == "tool_call":
        body = f"→ <code>{e['tool']}</code> {short(e.get('input'))}"
    elif k == "tool_result":
        out = e.get("output") or {}
        if e.get("error"):
            msg = out.get("message") if isinstance(out, dict) else out
            return (f'<tr class="err"><td>{e["t"]:.1f}s</td><td>{badge}</td><td>✗ <code>{e["tool"]}</code> '
                    f'<b>{html.escape(e["error"])}</b> {short(msg)}</td></tr>')
        status = out.get("status") if isinstance(out, dict) else None
        body = f"✓ <code>{e['tool']}</code> " + (f"<b>{status}</b> {out.get('application_id', '')}" if status else short(out, 160))
    elif k == "assistant_text":
        body = f'<span class="muted">💬 {short(e.get("text"), 300)}</span>'
    elif k == "handoff":
        body = f"⇢ <b>{e.get('src')} → {e.get('dst')}</b> {short(e.get('summary'), 300)}"
    elif k == "agent_end":
        body = f"<b>end</b> · turns {e.get('num_turns')} · tool calls {e.get('tool_calls')} · {(e.get('duration_ms') or 0) / 1000:.1f}s"
    elif k == "run_end":
        body = f"<b>finished</b><div class='final'>{html.escape(e.get('final_answer') or '')}</div>"
    else:
        return None
    return f"<tr><td>{e['t']:.1f}s</td><td>{badge}</td><td>{body}</td></tr>"


def main(path: Path) -> Path:
    events = [json.loads(x) for x in path.read_text(encoding="utf-8").split("\n") if x.strip()]
    rows = "\n".join(r for e in events if (r := row(e)))
    out = path.with_name("trace.html")
    out.write_text(f"""<!doctype html><html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Agent trace</title>
<style>
:root{{--bg:#f8fafc;--fg:#0f172a;--muted:#64748b;--line:#e2e8f0;--err:#fef2f2}}
@media (prefers-color-scheme: dark){{:root{{--bg:#0b1120;--fg:#e2e8f0;--muted:#94a3b8;--line:#1e293b;--err:#3b1212}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px}}
table{{border-collapse:collapse;width:100%}} td{{border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top}}
td:first-child{{color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}}
.badge{{color:#fff;border-radius:6px;padding:1px 8px;font-size:12px;white-space:nowrap}}
.err td{{background:var(--err)}} .muted{{color:var(--muted)}} code{{font-weight:600}}
.final{{white-space:pre-wrap;margin-top:6px;padding:8px;border-left:3px solid var(--line)}}
</style></head><body><h2>Trace · {html.escape(path.parent.name)}</h2><table>{rows}</table></body></html>""", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(main(Path(sys.argv[1])))
