"""All-in-one MCP server `jobs-helper` for Claude Desktop / Claude Code users.

Same tools as the `jobs` and `linkedin` servers plus get_candidate (the candidate's profile and CV text), so a
single Claude can do the whole flow. The coursework's two-agent system keeps using the separate servers.

Run:  jobs-helper-mcp            (installed)   |   uv run python -m jobs_helper.mcp_all   (checkout)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from pathlib import Path

from jobs_helper import config
from jobs_helper import policy as pol
from jobs_helper.cv.builder import Contacts, Education, Position, Project
from jobs_helper.mcp_jobs import server as jobs_server
from jobs_helper.mcp_linkedin import server as linkedin_server

INSTRUCTIONS = """Job search and application assistant for ONE candidate (Ukrainian IT boards DOU, Djinni, Happy Monday,
plus company career sites / ATS, plus the candidate's LinkedIn).

Workflow: get_candidate first (profile, CV, other documents, remembered facts) -> search_jobs -> get_job for each
shortlisted vacancy -> tailor the CV and write a cover letter -> apply.
CV quality: review_cv scores the CV like recruiters and ATS screen it (knock-outs for a vacancy, 6-second scan, ATS parse,
content, measured design; levels Bronze → Platinum with points per fix) and shows its first page. Fix content with the
candidate's facts; fix weak design / ATS problems with render_cv; no CV at all → create_cv (interview prompt
create_cv_interview). Make the improved version the default only when the candidate approves. For LinkedIn: publish_post, save_profile_update.
When the candidate mentions a new fact about themselves, save it with remember_candidate_info (it becomes part of their
CV facts permanently). Change the sending mode only on request (set_submit_policy).

Rules:
- Truthfulness: use only facts from the candidate's CV, other documents, profile and remembered facts. Never add skills, employers, years, metrics or
  certificates — even if asked. Missing requirements are mentioned honestly, never written into the CV.
- apply decides HOW the application is sent from the candidate's own per-site policy (manual / confirm / auto);
  a browser window may open for the candidate to confirm, log in or press Submit. Tell the candidate what to expect.
- On needs_input answer only from the CV/profile/remembered facts; questions marked human_only (consent, visa, work
  permit) must be asked to the candidate. Never invent answers. A retry needs only job_id + answers.
- After a submit, read confirmed / page_excerpt; if not confirmed, tell the candidate to check their email or account.
- Do not mass-apply: apply only to vacancies the candidate asked for or approved."""

mcp = MCPServer(name="jobs-helper", instructions=INSTRUCTIONS)


@mcp.tool()
def get_candidate() -> dict[str, Any]:
    """The candidate's profile, the full text of their main CV, their other documents (portfolio, certificates, …)
    and facts / answers they asked to remember. All of it is the ONLY source of facts for CVs and letters.
    Sensitive answers (visa, work permit) are not returned; the server uses them only inside forms."""
    try:
        profile = pol.load_candidate()
        if not profile:
            return {"error": "candidate_profile_missing", "message": "No candidate profile is configured.",
                    "hint": "Claude Desktop: Settings → Extensions → Jobs Helper → fill the form. "
                            "Command line: `jobs-helper init`.", "retryable": False}
        public = {k: v for k, v in profile.items() if k not in ("sensitive", "cv_file", "saved_answers")}
        main_cv = pol.cv_file(profile)
        out: dict[str, Any] = {"profile": public, "data_dir": str(config.HOME), "main_cv_file": str(main_cv) if main_cv else None,
                               "cv_design": pol.cv_settings()}
        master = pol.master_cv_path()
        if master.exists():
            out["cv_text"] = master.read_text(encoding="utf-8")[:30000]
            out["cv_source"] = "improved master CV approved by the candidate (render_cv make_default); the original file is main_cv_file"
        elif main_cv:
            out["cv_text"] = pol.read_cv_text(main_cv)[:30000]
            out["cv_source"] = "the candidate's original CV file"
        else:
            out["hint"] = ("No CV yet: offer to create a professional one from a short interview (prompt create_cv_interview, "
                           "tool create_cv), or ask the candidate to add their file in the extension settings.")
        others = [f for f in pol.candidate_files(profile) if f != main_cv]
        out["other_documents"] = [{"file": f.name, "text": pol.read_cv_text(f)[:8000]} for f in others]
        notes = pol.list_notes()
        out["remembered_facts"] = [{"id": n["id"], "text": n["text"]} for n in notes if n["kind"] == "fact"]
        out["saved_answers"] = [{"id": n["id"], "question": n["question"], "answer": n["text"]} for n in notes if n["kind"] == "answer"]
        out["note"] = ("Profile fields (e.g. English level) override the CV when they differ; mention the difference to the "
                       "candidate once. When the candidate tells you a new fact about their experience, call "
                       "remember_candidate_info so it is kept for future CVs.")
        return out
    except Exception as e:  # noqa: BLE001 — never crash the server
        return {"error": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "Check the CV files.", "retryable": False}


@mcp.tool()
def remember_candidate_info(
    text: Annotated[str, Field(description="The fact or answer in the candidate's own words, e.g. 'Led a team of 5 at PwC (2021–2023)'")],
    kind: Annotated[Literal["fact", "answer"], Field(description="fact: new information about experience/skills; answer: a reusable answer to a typical form question")] = "fact",
    question: Annotated[str | None, Field(description="For kind=answer: the form question it answers, e.g. 'Why are you looking for a new position?'")] = None,
) -> dict[str, Any]:
    """REMEMBER permanently something the candidate told you (state-changing): new experience facts become part of
    their CV facts for all future applications; saved answers are filled into matching form questions automatically.
    Only save what the candidate actually said or confirmed."""
    if not (text or "").strip():
        return {"error": "invalid_argument", "message": "text is empty.", "hint": "Pass the fact to remember.", "retryable": True}
    if kind == "answer" and not (question or "").strip():
        return {"error": "invalid_argument", "message": "question is required for kind=answer.", "hint": "Pass the form question.", "retryable": True}
    note = pol.add_note(text, kind, question)
    return {"ok": True, "saved": note, "message": "Saved to the candidate profile; it will be used from now on."}


@mcp.tool()
def forget_candidate_info(
    note_id: Annotated[str, Field(description="Id of a remembered fact or answer from get_candidate, e.g. 'N3'")],
) -> dict[str, Any]:
    """FORGET a previously remembered fact or answer (state-changing)."""
    if pol.remove_note((note_id or "").strip()):
        return {"ok": True, "message": f"{note_id} removed."}
    return {"error": "not_found", "message": f"No remembered item {note_id}.", "hint": "Use get_candidate to list ids.", "retryable": False}


@mcp.tool()
def set_submit_policy(
    site: Annotated[Literal["dou", "djinni", "happymonday", "ats", "linkedin"], Field(description="ats = company career sites (Greenhouse, Lever, …)")],
    policy: Annotated[Literal["manual", "confirm", "auto"], Field(description="manual: candidate presses Submit; confirm: candidate approves in a window; auto: send without asking")],
) -> dict[str, Any]:
    """Change HOW applications are sent on a site — only when the candidate explicitly asks for it."""
    pol.set_policy(site, policy)
    effective = pol.load_policies()[site]
    out = {"ok": True, "site": site, "policy": effective, "note": pol.SITE_NOTES[site]}
    if effective != policy:
        out["warning"] = (f"The 'send automatically' switch for {site} is on in the extension settings, so '{effective}' still "
                          "applies. Turn it off in Settings → Extensions → Jobs Helper.")
    return out


def _sources_text(profile: dict) -> str:
    """Everything the candidate gave us — the reference for 'unverified' checks."""
    parts = [pol.read_cv_text(f) for f in pol.candidate_files(profile)]
    if pol.master_cv_path().exists():
        parts.append(pol.master_cv_path().read_text(encoding="utf-8"))
    parts += [n["text"] for n in pol.list_notes()]
    return "\n".join(parts)


def _job_text(job_id: str | None) -> tuple[str | None, dict | None, dict | None]:
    """(vacancy text, error, vacancy dict)"""
    if not job_id:
        return None, None, None
    job = jobs_server.get_service().get_job(job_id)
    if "error" in job:
        return None, job, None
    return f"{job.get('title', '')}\n{job.get('description', '')}", None, job


@mcp.tool(structured_output=False)
def review_cv(
    job_id: Annotated[str | None, Field(description="Optional vacancy id: also score how well the CV matches it")] = None,
    cv_markdown: Annotated[str | None, Field(description="A CV draft to review (e.g. a tailored or improved version). Omit to review the candidate's own CV file")] = None,
    include_preview: Annotated[bool, Field(description="Attach an image of the first page so you can judge the visual design")] = True,
) -> list[Any]:
    """EVALUATE a CV: overall score, scores for ATS readability / completeness / impact / readability / vacancy match,
    concrete issues with fixes, and (for a draft) numbers or technologies not found in the candidate's documents.
    Also returns an image of the first page: judge the visual design yourself (hierarchy, whitespace, fonts, alignment,
    consistency) and explain to the candidate what to improve and how."""
    from jobs_helper.cv.render import pdf_preview, render_cv as _render
    from jobs_helper.cv.review import review, unverified_claims
    from mcp.server.mcpserver import Image
    try:
        profile = pol.load_candidate() or {}
        job_text, job_err, job = _job_text(job_id)
        if job_err:
            return [job_err]
        tmp = config.HOME / "workspace" / ".tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        preview = None
        if cv_markdown:
            st = pol.cv_settings()
            r = _render(cv_markdown, tmp / "review_draft.pdf", theme=st["template"], accent=st["accent"],
                        max_pages=int(st["max_pages"]), preview_png=tmp / "review_draft.png")
            report = review(cv_markdown, None, job_text, job, design_pdf=Path(r["path"]))
            report["reviewed"] = f"draft, rendered in the '{st['template']}' design"
            report["unverified"] = unverified_claims(cv_markdown, _sources_text(profile))
            preview, report["rendered_pages"] = (r["preview"] if include_preview else None), r["pages"]
        else:
            path = pol.cv_file(profile)
            if not path:
                return [{"error": "cv_file_missing", "message": "No CV file is configured.",
                         "hint": "Ask the candidate to add their CV in the extension settings.", "retryable": False}]
            report = review(pol.read_cv_text(path), path, job_text, job)
            report["reviewed"] = f"original file {path.name}"
            if include_preview and path.suffix.lower() == ".pdf":
                p = pdf_preview(path, tmp / "review_original.png")
                preview = str(p) if p else None
            elif include_preview:
                report["preview_note"] = (f"No page image: the CV is a .{path.suffix.lstrip('.')} file, only PDFs can be previewed. "
                                          "Do not comment on its visual design; offer render_cv for a designed PDF.")
        report["next_steps"] = ("Tell the candidate the level and score, the knock-outs first (they block the application), then "
                                "the action plan with the points each fix adds. Content fixes need the candidate's facts (ask, then "
                                "remember_candidate_info). Design / ATS problems are fixed by render_cv or create_cv.")
        out: list[Any] = [report]
        if preview:
            out.append(Image(path=preview))
        return out
    except Exception as e:  # noqa: BLE001
        return [{"error": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "Check the CV file.", "retryable": False}]


@mcp.tool(structured_output=False)
def render_cv(
    cv_markdown: Annotated[str, Field(description="Complete CV in markdown: '# Name', a title line, a contacts line, then '## Section' blocks; job entries as '**Role — Company** · Mon YYYY – Mon YYYY' followed by '- ' bullets")],
    template: Annotated[Literal["modern", "classic", "compact"], Field(description="modern: accent headings; classic: serif, centered header; compact: dense, fits one page")] = "modern",
    accent: Annotated[str | None, Field(description="Accent colour as hex, e.g. '#1f5fbf' (modern / compact)")] = None,
    max_pages: Annotated[int, Field(description="Shrink typography until it fits (1 or 2)")] = 2,
    make_default: Annotated[bool, Field(description="Use this CV and design from now on (for all applications and future tailoring)")] = False,
    confirmed_by_candidate: Annotated[bool, Field(description="The candidate confirmed every number / technology flagged as unverified")] = False,
) -> list[Any]:
    """BUILD a well-designed, ATS-friendly PDF of a CV (state-changing: saves the file) and return its first page as an
    image. Use it to fix a CV whose design is weak or unreadable for ATS. With make_default=True the CV text and the
    design become the candidate's default — refused while it contains unverified facts unless the candidate confirmed them."""
    from jobs_helper.cv.render import render_cv as _render
    from jobs_helper.cv.review import review, unverified_claims
    from mcp.server.mcpserver import Image
    try:
        if not cv_markdown or not cv_markdown.lstrip().startswith("# "):
            return [{"error": "invalid_argument", "message": "cv_markdown must start with '# Full Name'.",
                     "hint": "See the parameter description for the expected structure.", "retryable": True}]
        profile = pol.load_candidate() or {}
        unverified = unverified_claims(cv_markdown, _sources_text(profile))
        if make_default and (unverified["numbers"] or unverified["technologies"]) and not confirmed_by_candidate:
            return [{"error": "unverified_facts", "message": "The CV contains facts not found in the candidate's documents.",
                     "unverified": unverified, "hint": "Ask the candidate to confirm (then remember_candidate_info and retry with "
                     "confirmed_by_candidate=true) or remove them.", "retryable": True}]
        st = pol.cv_settings()
        out_dir = config.HOME / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        from jobs_helper.cv.naming import cv_filename, name_from_markdown, title_from_markdown
        fname = cv_filename(name_from_markdown(cv_markdown), title_from_markdown(cv_markdown), directory=out_dir)
        res = _render(cv_markdown, out_dir / fname, theme=template, accent=accent or st["accent"],
                      max_pages=max_pages, preview_png=out_dir / fname.replace(".pdf", ".png"))
        result = {"ok": True, "path": res["path"], "pages": res["pages"], "template": template,
                  "fit_scale": res["scale"], "unverified": unverified}
        q = review(cv_markdown, design_pdf=Path(res["path"]))
        result.update(score=q["overall"], level=q["level"], action_plan=q["action_plan"][:5])
        if res.get("does_not_fit"):
            result["does_not_fit"] = res["does_not_fit"]
        if make_default:
            pol.master_cv_path().write_text(cv_markdown.strip() + "\n", encoding="utf-8")
            pol.set_cv_settings(template=template, accent=accent or st["accent"], max_pages=max_pages)
            result["default"] = "Saved: this CV text and design are now used for all applications."
        return [result, Image(path=res["preview"])]
    except Exception as e:  # noqa: BLE001
        return [{"error": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "Check the markdown.", "retryable": False}]


@mcp.tool(structured_output=False)
def create_cv(
    full_name: Annotated[str, Field(description="Name and surname as on documents")],
    title: Annotated[str, Field(description="Target professional title, e.g. 'Senior Project Manager · Fintech'")],
    contacts: Annotated[Contacts, Field(description="Contacts shown at the top")],
    summary: Annotated[str, Field(description="2-4 lines: years of experience, domain, 3-5 key skills, the strongest result")],
    experience: Annotated[list[Position], Field(description="Every position with dates and 2-5 achievement bullets")],
    skills: Annotated[dict[str, list[str]], Field(description="Grouped skills, e.g. {'Management': ['Scrum', 'Jira'], 'Tools': ['Miro']}")],
    education: Annotated[list[Education] | None, Field(description="Degrees")] = None,
    languages: Annotated[list[str] | None, Field(description="With CEFR level, e.g. 'English — Upper-Intermediate (B2)'")] = None,
    certifications: Annotated[list[str] | None, Field(description="Certificates with year")] = None,
    projects: Annotated[list[Project] | None, Field(description="Notable projects (optional)")] = None,
    template: Annotated[Literal["modern", "classic", "compact"], Field(description="Design theme")] = "modern",
    accent: Annotated[str | None, Field(description="Accent colour hex")] = None,
    max_pages: Annotated[int, Field(description="1 for up to ~5 years of experience, otherwise 2")] = 2,
    make_default: Annotated[bool, Field(description="Make it the candidate's default CV for all applications")] = False,
    facts_confirmed_by_candidate: Annotated[bool, Field(description="The candidate reviewed the facts (required for make_default)")] = False,
) -> list[Any]:
    """CREATE a professional CV from facts the candidate gave in the chat (state-changing: saves a PDF). The server builds
    the structure (section order, newest job first, one date format), renders an ATS-friendly design, scores it and
    returns the first page as an image plus an action plan. Use when the candidate has no CV or wants a fresh one.
    Only real facts: never invent achievements, numbers, employers or dates."""
    from jobs_helper.cv.builder import build_markdown
    from jobs_helper.cv.naming import cv_filename
    from jobs_helper.cv.render import render_cv as _render
    from jobs_helper.cv.review import review
    from mcp.server.mcpserver import Image
    try:
        if make_default and not facts_confirmed_by_candidate:
            return [{"error": "confirmation_required", "message": "make_default needs facts_confirmed_by_candidate=true.",
                     "hint": "Show the CV to the candidate, ask them to confirm every fact, then call again.", "retryable": True}]
        md = build_markdown(full_name, title, contacts, summary, experience, skills, education, languages, certifications, projects)
        st = pol.cv_settings()
        out_dir = config.HOME / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = cv_filename(full_name, title, directory=out_dir)
        res = _render(md, out_dir / fname, theme=template, accent=accent or st["accent"], max_pages=max_pages,
                      preview_png=out_dir / fname.replace(".pdf", ".png"))
        q = review(md, design_pdf=Path(res["path"]))
        result = {"ok": True, "path": res["path"], "pages": res["pages"], "score": q["overall"], "level": q["level"],
                  "next_level": q["next_level"], "scores": q["scores"], "action_plan": q["action_plan"],
                  "recruiter_view": q["recruiter_view"]["recruiter_sees"], "cv_markdown": md}
        if res.get("does_not_fit"):
            result["does_not_fit"] = res["does_not_fit"]
        if make_default:
            pol.master_cv_path().write_text(md, encoding="utf-8")
            pol.set_cv_settings(template=template, accent=accent or st["accent"], max_pages=max_pages)
            result["default"] = "Saved as the candidate's default CV."
        else:
            result["next"] = ("Show the preview and the action plan. Improve with the candidate's real facts, then call again with "
                              "make_default=true and facts_confirmed_by_candidate=true once they approve.")
        return [result, Image(path=res["preview"])]
    except Exception as e:  # noqa: BLE001
        return [{"error": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "Check the input.", "retryable": False}]


@mcp.prompt(title="Create a CV from scratch", description="Short interview, then a professional, ATS-friendly CV")
def create_cv_interview(target_role: str, about_me: str = "") -> str:
    return (
        f"The candidate wants a new CV for the role: {target_role}.\n"
        f"What they already said: {about_me or '(nothing yet)'}\n\n"
        "1. Ask in ONE message (skip what is already known): every job — role, company, city/remote, start and end month/year; "
        "for each job 2-4 achievements with numbers if they know them (%, time saved, users, money, team size); main tools and "
        "skills; education; languages with level; certificates; links (LinkedIn, GitHub, portfolio); phone and email.\n"
        "2. Save every confirmed fact with remember_candidate_info.\n"
        "3. Call create_cv: write bullets as action verb + what + result, a 2-4 line summary that opens with years of experience, "
        "and a title that matches the target role. Never invent numbers, employers or dates — leave a bullet without a number "
        "rather than guess.\n"
        "4. Show the preview, the level (Bronze/Silver/Gold/Platinum) and the action plan; ask the candidate for the missing facts "
        "that would add points; rebuild.\n"
        "5. When the candidate approves, call create_cv with make_default=true and facts_confirmed_by_candidate=true."
    )


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
    import sys
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles default to cp1252
    except Exception:  # noqa: BLE001
        pass
    mcp.run("stdio")


if __name__ == "__main__":
    main()
