"""Browser apply backend (Playwright, the candidate's own persistent browser profile).

Flow: open the vacancy -> site-specific step that reveals the form -> extract the form schema ->
plan values (planner.py) -> fill -> act on the submit policy:
  dry_run  fill + screenshot, never submit (headless)
  manual   fill, show a banner, the candidate presses the site's own Submit button
  confirm  fill, show "Submit / Cancel" panel inside the page, submit on "Submit"
  auto     fill and submit

Env: JOBS_HEADLESS=1 forces headless (tests), JOBS_HUMAN_TIMEOUT seconds to wait for the human (default 900).
"""

from __future__ import annotations

import os
import re
import uuid
from difflib import SequenceMatcher
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from jobs_helper import config
from jobs_helper.apply.backend import ApplyOutcome, ApplyRequest
from jobs_helper.apply.fields import FormField
from jobs_helper.apply.page_js import CONFIRM_BANNER, EXTRACT_FORM, SUCCESS_RE, VALIDATION_ERRORS
from jobs_helper.apply.pdf import render_pdfs
from jobs_helper.apply.planner import plan_fill
from jobs_helper.tracker import workspace

PROFILES_DIR = config.ROOT / ".browser_profiles"
APPLY_BUTTON = re.compile(r"^\s*(apply( now| for this (job|position))?|i'?m interested|подати( заявку)?|податися( на вакансію)?|"
                          r"відгукнутися( на вакансію)?|відгукнутись|откликнуться)\s*$", re.I)
COOKIE_BUTTON = re.compile(r"^\s*(accept( all)?( cookies)?|allow all|agree|i agree|got it|ok|decline all|reject all|"
                           r"прийняти( все)?|погоджуюсь|зрозуміло|дозволити)\s*$", re.I)
TRUTHY = {"yes", "true", "1", "так", "y"}


def _err(code, message, hint="", retryable=False):
    return {"error": code, "message": message, "hint": hint, "retryable": retryable}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9а-яіїєґ]+", "_", text.lower()).strip("_")[:40] or "field"


class BrowserBackend:
    def __init__(self, headless: bool | None = None):
        env = os.environ.get("JOBS_HEADLESS")
        self.force_headless = headless if headless is not None else (env.lower() in ("1", "true", "yes") if env else None)
        self.human_timeout = int(os.environ.get("JOBS_HUMAN_TIMEOUT", "900")) * 1000

    # ------------------------------------------------------------ entry point
    def apply(self, req: ApplyRequest) -> ApplyOutcome:
        start_url = req.job.apply_url if req.target.startswith("ats:") or req.target == "company_site" else req.job.url
        if not start_url:
            return ApplyOutcome("failed", error=_err("external_apply", "No application URL is known.", "Open the vacancy manually."))
        tmp = workspace() / ".tmp" / uuid.uuid4().hex[:10]
        pdfs = render_pdfs({"cv": req.cv_markdown, "cover_letter": req.cover_letter}, tmp)
        headless = self.force_headless if self.force_headless is not None else req.dry_run
        profile = PROFILES_DIR / (req.target if req.target in ("dou", "djinni", "happymonday") else "ats")
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(str(profile), headless=headless, locale="uk-UA",
                                                       viewport={"width": 1280, "height": 900})
            try:
                return self._run(ctx, req, start_url, pdfs, tmp)
            except PWTimeout as e:
                return ApplyOutcome("failed", error=_err("form_changed", f"Page did not behave as expected: {str(e)[:160]}",
                                                         "Hand the prepared documents and the link to the candidate.", False))
            finally:
                ctx.close()

    # ------------------------------------------------------------ main flow
    def _run(self, ctx, req: ApplyRequest, start_url: str, pdfs: dict[str, Path], tmp: Path) -> ApplyOutcome:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        self._dismiss_cookies(page)

        blocked = self._open_form(page, ctx, req)
        if isinstance(blocked, ApplyOutcome):
            return blocked
        page = blocked
        frame, schema = self._find_form(page, req.target)
        if not schema["fields"]:
            self._shot(page, tmp, "no_form")
            return ApplyOutcome("failed", screenshot=str(tmp / "no_form.png"), error=_err(
                "external_apply", "No application form could be found on the page.",
                "Give the candidate the link and the prepared documents.", False) | {"url": page.url})

        fields, dom = self._to_fields(schema["fields"])
        cover = req.cover_letter
        if req.target == "djinni" and frame.locator("input[name=anon_apply]").count():
            # anonymous Djinni apply: the message itself must contain a LinkedIn profile (or the candidate logs in)
            linkedin = (req.profile.get("links") or {}).get("linkedin")
            if not linkedin:
                return ApplyOutcome("failed", error=_err("not_logged_in", "Anonymous Djinni apply needs a LinkedIn URL.",
                    "Add links.linkedin to candidate.yaml or run `uv run python -m jobs_helper login djinni`."))
            cover = f"{cover.rstrip()}\n\nLinkedIn: {linkedin}"
        plan = plan_fill(fields, req.profile, req.answers, req.job.source, req.cv_markdown, cover)
        if not plan.complete:
            return ApplyOutcome("needs_input", filled=plan.origin, questions=plan.questions,
                                message=f"{len(plan.questions)} required field(s) need an answer.")
        for f in fields:
            if f.id in plan.values:
                self._fill(frame, dom[f.id], f, plan.values[f.id], pdfs)
        page.wait_for_timeout(500)
        shot = self._shot(page, tmp, "filled")

        if req.dry_run:
            return ApplyOutcome("prepared", filled=plan.origin, screenshot=shot, message="Form filled (dry run): nothing was sent.")
        if not schema["submit"]:
            return ApplyOutcome("failed", filled=plan.origin, screenshot=shot,
                                error=_err("form_changed", "Submit button not found.", "Let the candidate finish manually."))

        if req.policy == "manual":
            frame.evaluate(CONFIRM_BANNER, ["Форму заповнено", "Перевірте дані й натисніть кнопку відправки на сторінці самостійно. "
                                            "Вікно можна закрити, якщо передумали.", None, None])
            return self._await_human_submit(page, plan.origin, shot)
        if req.policy == "confirm":
            frame.evaluate(CONFIRM_BANNER, ["Надіслати відгук?", f"{req.job.title} — {req.job.company or ''}. Перевірте заповнену форму.",
                                            "Надіслати", "Скасувати"])
            try:
                frame.wait_for_function("() => window.__jh_decision", timeout=self.human_timeout)
                decision = frame.evaluate("() => window.__jh_decision")
            except PWTimeout:
                return ApplyOutcome("declined", filled=plan.origin, screenshot=shot, message="No answer from the candidate; nothing was sent.")
            if decision != "yes":
                return ApplyOutcome("declined", filled=plan.origin, screenshot=shot, message="The candidate cancelled the submission.")
        if schema["captcha"] and req.policy == "auto":
            return ApplyOutcome("failed", filled=plan.origin, screenshot=shot, error=_err(
                "captcha_required", "The form shows a captcha.", "Switch this site to 'manual' or let the candidate submit."))
        return self._submit(page, frame, plan.origin, tmp)

    # ------------------------------------------------------------ site steps
    def _open_form(self, page: Page, ctx, req: ApplyRequest):
        t = req.target
        if t == "happymonday":
            btn = page.locator("a.js_job_click").first
            if btn.count() and "/profile/login" in (btn.get_attribute("href") or ""):
                return ApplyOutcome("failed", error=_err("not_logged_in", "No Happy Monday session.",
                                                         "Ask the candidate to run `uv run python -m jobs_helper login happymonday`."))
            return self._click_apply(page, ctx, btn if btn.count() else None)
        if t == "dou":
            btn = page.locator("a[href='#apply'], .reply a").first
            page = self._click_apply(page, ctx, btn if btn.count() else None)
            body = page.inner_text("body")[:20000]
            if re.search(r"увійдіть|зареєструйтесь|login|sign in", body, re.I) and not page.locator("textarea:visible").count():
                return ApplyOutcome("failed", error=_err("not_logged_in", "No DOU session.",
                                                         "Ask the candidate to run `uv run python -m jobs_helper login dou`."))
            return page
        if t == "djinni":
            btn = page.locator("button.js-inbox-toggle-reply-form").first
            if btn.count() and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1200)
            return page
        return page  # ATS / company site: handled by _find_form

    def _click_apply(self, page: Page, ctx, btn) -> Page:
        before = len(ctx.pages)
        if btn is None:
            btn = page.get_by_role("button", name=APPLY_BUTTON).or_(page.get_by_role("link", name=APPLY_BUTTON)).first
            if not btn.count():
                return page
        try:
            btn.click(timeout=8000)
        except PWTimeout:
            self._dismiss_cookies(page)
            btn.click(timeout=8000)
        page.wait_for_timeout(2500)
        if len(ctx.pages) > before:            # opened in a new tab
            page = ctx.pages[-1]
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)
        self._dismiss_cookies(page)
        return page

    def _find_form(self, page: Page, target: str) -> tuple[Frame, dict]:
        root = "form.js-inbox-reply-form" if target == "djinni" else None
        best_frame, best = page.main_frame, page.evaluate(EXTRACT_FORM, root)
        if len(best["fields"]) < 2 and target not in ("djinni", "dou"):
            page2 = self._click_apply(page, page.context, None)
            best_frame, best = page2.main_frame, page2.evaluate(EXTRACT_FORM, None)
            page = page2
        if len(best["fields"]) < 2:
            for fr in page.frames[1:]:
                try:
                    s = fr.evaluate(EXTRACT_FORM, None)
                except Exception:  # noqa: BLE001 — cross-origin or detached frames
                    continue
                if len(s["fields"]) > len(best["fields"]):
                    best_frame, best = fr, s
        return best_frame, best

    # ------------------------------------------------------------ fields
    def _to_fields(self, raw: list[dict]) -> tuple[list[FormField], dict[str, dict]]:
        fields, dom, seen = [], {}, set()
        for r in raw:
            fid = r["name"] if r["name"] and not r["name"].startswith(("f", "_")) else _slug(r["label"])
            base, n = fid, 2
            while fid in seen:
                fid, n = f"{base}_{n}", n + 1
            seen.add(fid)
            fields.append(FormField(fid, r["label"] or r["name"], r["type"], bool(r["required"]), r.get("options") or []))
            dom[fid] = r
        return fields, dom

    def _fill(self, frame: Frame, raw: dict, f: FormField, value: str, pdfs: dict[str, Path]) -> None:
        sel = lambda i: f'[data-jh-id="{i}"]'  # noqa: E731
        el = frame.locator(sel(raw["dom"][0]))
        if f.type == "file":
            el.set_input_files(str(pdfs["cover_letter" if f.key == "cover_letter" else "cv"]))
        elif f.type == "radio":
            idx = _best_option(raw["options"], value)
            if idx is not None:
                _check(frame.locator(sel(raw["dom"][idx])))
        elif f.type == "checkbox":
            if str(value).strip().lower() in TRUTHY:
                _check(el)
        elif f.type == "select":
            idx = _best_option(raw["options"], value)
            if idx is not None:
                el.select_option(label=raw["options"][idx])
        else:
            el.fill(value)

    # ------------------------------------------------------------ submit / wait
    def _submit(self, page: Page, frame: Frame, filled: dict, tmp: Path) -> ApplyOutcome:
        url_before = page.url
        frame.locator("[data-jh-submit]").first.click(timeout=10000)
        try:
            page.wait_for_function(
                "([re, before]) => location.href !== before || new RegExp(re, 'i').test(document.body.innerText)",
                arg=[SUCCESS_RE, url_before], timeout=20000)
        except PWTimeout:
            invalid = frame.evaluate(VALIDATION_ERRORS)
            shot = self._shot(page, tmp, "after_submit")
            if invalid:
                return ApplyOutcome("failed", filled=filled, screenshot=shot, error=_err(
                    "form_validation", f"The site rejected {len(invalid)} field(s) after submit.",
                    "Check the screenshot; let the candidate finish manually.", False))
            return ApplyOutcome("submitted", filled=filled, screenshot=shot,
                                message="Submit clicked; the site showed no explicit confirmation (check the screenshot).")
        return ApplyOutcome("submitted", filled=filled, screenshot=self._shot(page, tmp, "submitted"), message="Application submitted.")

    def _await_human_submit(self, page: Page, filled: dict, shot: str) -> ApplyOutcome:
        try:
            page.wait_for_function("(re) => new RegExp(re, 'i').test(document.body.innerText)", arg=SUCCESS_RE,
                                   timeout=self.human_timeout)
            return ApplyOutcome("submitted", filled=filled, screenshot=shot, message="The candidate submitted the form.")
        except PWTimeout:
            return ApplyOutcome("awaiting_user", filled=filled, screenshot=shot,
                                message="Form filled; no submission confirmed within the waiting time.")
        except Exception:  # noqa: BLE001 — the candidate closed the window
            return ApplyOutcome("declined", filled=filled, screenshot=shot, message="The candidate closed the form without sending.")

    # ------------------------------------------------------------ helpers
    def _dismiss_cookies(self, page: Page) -> None:
        try:
            btn = page.get_by_role("button", name=COOKIE_BUTTON).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=2000)
                page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001 — banners are optional
            pass

    def _shot(self, page: Page, tmp: Path, name: str) -> str:
        path = tmp / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception:  # noqa: BLE001
            return ""
        return str(path)


def _check(locator) -> None:
    """Custom widgets hide the real input; fall back to a programmatic click that fires change events."""
    try:
        locator.check(force=True, timeout=3000)
    except Exception:  # noqa: BLE001
        locator.evaluate("el => { if (!el.checked) el.click(); }")


def _best_option(options: list[str], value: str) -> int | None:
    if not options:
        return None
    v = value.strip().lower()
    scored = [(SequenceMatcher(None, v, o.lower()).ratio() + (0.5 if v and (v in o.lower() or o.lower() in v) else 0), i)
              for i, o in enumerate(options)]
    score, idx = max(scored)
    return idx if score >= 0.45 else None
