"""All-in-one MCP server `jobs-helper` for Claude Desktop / Claude Code users.

Same tools as the `jobs` and `linkedin` servers plus get_candidate (the candidate's profile and CV text), so a
single Claude can do the whole flow. The coursework's two-agent system keeps using the separate servers.

Run:  jobs-helper-mcp            (installed)   |   uv run python -m jobs_helper.mcp_all   (checkout)
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from pathlib import Path

from jobs_helper import config
from jobs_helper import policy as pol
from jobs_helper import mcp_career
from jobs_helper.cv.builder import Contacts, Education, Position, Project
from jobs_helper.mcp_jobs import server as jobs_server
from jobs_helper.mcp_linkedin import server as linkedin_server

INSTRUCTIONS = """Job search and application assistant for ONE candidate (Ukrainian IT boards DOU, Djinni, Happy Monday,
plus company career sites / ATS, plus the candidate's LinkedIn).

Workflow: get_candidate first (profile, CV, other documents, remembered facts) -> search_jobs -> get_job for each
shortlisted vacancy -> tailor the CV and write a cover letter -> apply.
LinkedIn: read_vacancy reads a LinkedIn job link the candidate pastes (Easy Apply stays with the candidate);
import_linkedin_profile turns their LinkedIn PDF / data archive into a CV draft; save_profile_update prepares profile
texts from the CV and, after approval, opens a copy-helper (no automatic changes on LinkedIn).
Search: search_jobs(sort_by="fit") ranks by fit with the CV; get_job adds fit and warnings (red / green flags) — mention
high-risk warnings before applying. saved_search keeps standing queries and shows only new vacancies.
Research: company_insights (DOU rating, employee-review themes, spheres), salary_insights (DOU salary survey),
market_skill_gap (what the market asks for a role vs the CV, learning plan).
Tables: job_search_tables (applications, vacancies seen, CV history, weekly activity) shown as markdown tables;
open_dashboard for the full local page; cv_history to show / compare / restore CV versions.
After applying: pipeline_report (stages, funnel, follow-ups due); update_application when the candidate reports news
(also for applications made elsewhere). Interviews: interview_prep(job_id), then the mock_interview prompt.
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
            from jobs_helper import cv_history
            cv_history.ensure_baseline()
            pol.master_cv_path().write_text(cv_markdown.strip() + "\n", encoding="utf-8")
            pol.set_cv_settings(template=template, accent=accent or st["accent"], max_pages=max_pages)
            v = cv_history.record(cv_markdown, "made default (render_cv)", "render_cv", template, res["path"], q["overall"], q["level"])
            result["default"] = f"Saved as CV version v{v['version']}: this CV text and design are now used for all applications."
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
            from jobs_helper import cv_history
            cv_history.ensure_baseline()
            pol.master_cv_path().write_text(md, encoding="utf-8")
            pol.set_cv_settings(template=template, accent=accent or st["accent"], max_pages=max_pages)
            v = cv_history.record(md, "created (create_cv)", "create_cv", template, res["path"], q["overall"], q["level"])
            result["default"] = f"Saved as the candidate's default CV (version v{v['version']})."
        else:
            result["next"] = ("Show the preview and the action plan. Improve with the candidate's real facts, then call again with "
                              "make_default=true and facts_confirmed_by_candidate=true once they approve.")
        return [result, Image(path=res["preview"])]
    except Exception as e:  # noqa: BLE001
        return [{"error": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "Check the input.", "retryable": False}]


@mcp.tool()
def read_vacancy(
    url: Annotated[str | None, Field(description="Link to a vacancy: LinkedIn job, Work.ua, a company career page, an ATS page")] = None,
    text: Annotated[str | None, Field(description="Or the vacancy text pasted by the candidate")] = None,
    title: Annotated[str | None, Field(description="Vacancy title when only text is given (optional)")] = None,
    company: Annotated[str | None, Field(description="Company when only text is given (optional)")] = None,
) -> dict[str, Any]:
    """READ a vacancy the candidate found themselves (LinkedIn link, any job site or pasted text) and check the current CV
    against it: knock-out filters (years, English, keywords), coverage, what is missing. Returns a job_id usable with
    get_job, review_cv and apply. LinkedIn: only the link the candidate gave is opened; applying on LinkedIn (Easy Apply)
    stays with the candidate."""
    from jobs_helper.ats import apply_target
    from jobs_helper.cv.review import review
    from jobs_helper.sources.base import SourceError
    from jobs_helper.sources.external import read_vacancy as _read
    try:
        job = _read(url=url, text=text, title=title, company=company)
    except SourceError as e:
        return {"error": e.code, "message": e.message, "hint": e.hint, "retryable": e.code in ("source_unavailable", "invalid_argument")}
    out: dict[str, Any] = {"job_id": job.id, "vacancy": job.short() | {"description": job.description[:3000]},
                           "apply_target": apply_target(job)}
    try:
        from jobs_helper import activity
        from jobs_helper.insights.redflags import vacancy_flags
        out["warnings"] = vacancy_flags(job)
        activity.record_opened(job, None, out["warnings"])
    except Exception:  # noqa: BLE001
        pass
    profile = pol.load_candidate() or {}
    cv_text = (pol.master_cv_path().read_text(encoding="utf-8") if pol.master_cv_path().exists()
               else (pol.read_cv_text(f) if (f := pol.cv_file(profile)) else ""))
    if cv_text:
        r = review(cv_text, None, f"{job.title}\n{job.description}", {"title": job.title, "experience_years": job.experience_years})
        m = r["details"].get("match", {})
        out["cv_fit"] = {"score_for_this_vacancy": r["overall"], "level": r["level"], "knockouts": r["knockouts"],
                         "keywords_covered": m.get("covered"), "keywords_missing": m.get("missing"),
                         "top_fixes": [a["fix"] for a in r["action_plan"] if a["category"] in ("match", "first_impression")][:4]}
    out["next_steps"] = ("Propose a CV for this vacancy: reorder and rephrase the candidate's real experience toward its "
                         "requirements (never add missing skills), check it with review_cv(job_id, cv_markdown), show the preview "
                         "with render_cv, then apply(job_id). " + ("For LinkedIn Easy Apply hand the PDF and the link to the candidate."
                                                                   if out["apply_target"] == "not_a_form:linkedin" else ""))
    return out


@mcp.tool(structured_output=False)
def import_linkedin_profile(
    path: Annotated[str | None, Field(description="LinkedIn 'Save to PDF' file or the data archive (.zip / folder). Omit to look among the candidate's documents")] = None,
    build_cv: Annotated[bool, Field(description="Also build a draft CV from the profile and return its preview")] = True,
    template: Annotated[Literal["modern", "classic", "compact"], Field(description="Design of the draft CV")] = "modern",
) -> list[Any]:
    """IMPORT the candidate's LinkedIn profile from a file LinkedIn lets them download (profile → More → Save to PDF, or
    Settings → Data privacy → Get a copy of your data) and turn it into a CV draft. The profile is kept as one of the
    candidate's fact sources. The draft is NOT made default — show it, fix with the candidate, then create_cv(make_default)."""
    import shutil
    from jobs_helper.cv.builder import build_markdown
    from jobs_helper.cv.naming import cv_filename
    from jobs_helper.cv.render import render_cv as _render
    from jobs_helper.cv.review import review
    from jobs_helper.linkedin.profile_import import import_profile, to_cv_args
    from mcp.server.mcpserver import Image
    try:
        profile = pol.load_candidate() or {}
        src = Path(path).expanduser() if path else next(
            (f for f in pol.candidate_files(profile) if re.search(r"linkedin|profile", f.name, re.I)), None)
        if src is None or not src.exists():
            return [{"error": "file_missing", "message": "No LinkedIn profile file found.",
                     "hint": "Ask the candidate to open their LinkedIn profile → More → Save to PDF (or download the data archive) "
                             "and add the file in the extension settings or give its path.", "retryable": True}]
        data = import_profile(src)
        docs = config.HOME / "documents"
        docs.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() == ".pdf" and docs not in src.parents:
            shutil.copy(src, docs / "linkedin_profile.pdf")
        complete = bool(data.get("full_name") and data.get("positions"))
        out: dict[str, Any] = {"profile": {k: v for k, v in data.items() if k != "raw_text"}, "parsed_completely": complete}
        if not complete and data.get("raw_text"):
            out["raw_text"] = data["raw_text"][:6000]
            out["hint"] = "The PDF layout was not fully recognised: structure the facts from raw_text yourself and call create_cv."
        if build_cv and complete:
            args = to_cv_args(data)
            personal = profile.get("personal") or {}
            if personal.get("email") and "example.com" in args["contacts"].email:
                args["contacts"].email = personal["email"]
            if personal.get("phone") and not args["contacts"].phone:
                args["contacts"].phone = personal["phone"]
            md = build_markdown(**args)
            out_dir = config.HOME / "generated"
            out_dir.mkdir(parents=True, exist_ok=True)
            fname = cv_filename(args["full_name"], args["title"], directory=out_dir, extra="LinkedIn")
            st = pol.cv_settings()
            res = _render(md, out_dir / fname, theme=template, accent=st["accent"], max_pages=2,
                          preview_png=out_dir / fname.replace(".pdf", ".png"))
            q = review(md, design_pdf=Path(res["path"]))
            out.update(cv_markdown=md, path=res["path"], score=q["overall"], level=q["level"], action_plan=q["action_plan"][:6],
                       next="LinkedIn descriptions are often thin: ask the candidate for numbers / results, then create_cv "
                            "(make_default=true, facts_confirmed_by_candidate=true) when they approve.")
            return [out, Image(path=res["preview"])]
        return [out]
    except Exception as e:  # noqa: BLE001
        return [{"error": "internal_error", "message": f"{type(e).__name__}: {e}", "hint": "Check the file.", "retryable": False}]


@mcp.prompt(title="CV for a vacancy", description="Read a vacancy (LinkedIn or any link / text) and propose a tailored CV")
def tailor_cv_for_vacancy(vacancy: str) -> str:
    return (
        f"Vacancy (link or text): {vacancy}\n\n"
        "1. read_vacancy with the link (or the text). Tell the candidate the fit: knock-outs first, then missing keywords.\n"
        "2. get_candidate. Write a CV for this vacancy from the candidate's REAL facts only: title close to the vacancy wording "
        "if truthful, summary that opens with years and the 3-5 skills the vacancy wants, most relevant bullets first.\n"
        "3. review_cv(job_id, cv_markdown) — iterate until no fix depends on facts you do not have; ask the candidate for those.\n"
        "4. render_cv to show the design; on approval apply(job_id) — or, for LinkedIn Easy Apply, give the candidate the PDF."
    )


@mcp.prompt(title="Update LinkedIn from my CV", description="Prepare LinkedIn headline / about / skills / experience from the CV, with approval")
def linkedin_profile_from_cv(focus: str = "") -> str:
    return (
        f"Goal: update the candidate's LinkedIn profile from their CV. Focus / target role: {focus or '(as in the CV)'}\n\n"
        "1. get_candidate (CV + remembered facts are the only source).\n"
        "2. Draft: headline ≤ 220 chars (role · specialisation · 2-3 key skills), About 3 short paragraphs in first person "
        "(who you are, top results with numbers, what you look for), 20-30 skills, and a description for every position.\n"
        "3. save_profile_update(..., approved_by_candidate=false) and show the draft; fix anything the candidate changes.\n"
        "4. Only after an explicit 'yes': save_profile_update(..., approved_by_candidate=true) — a copy-helper opens next to the "
        "profile and the candidate pastes each block. Nothing is changed on LinkedIn automatically."
    )


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
           linkedin_server.get_profile, linkedin_server.publish_post, linkedin_server.save_profile_update, *mcp_career.TOOLS):
    mcp.tool()(fn)
mcp.prompt(title="Mock interview", description="Interview practice for a vacancy: one question at a time with feedback")(
    mcp_career.mock_interview)

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
