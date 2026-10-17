"""Logic behind the `linkedin` MCP tools: profile, posts (per the candidate's policy), profile update text.

Posting policy (settings.yaml, site 'linkedin'):
  manual   open LinkedIn's own share dialog with the text pre-filled; the candidate publishes it
  confirm  show a preview window with "Publish / Cancel"; publish through the API on "Publish"
  auto     publish through the API immediately
JOBS_DRY_RUN=1 only records the draft. Editing a LinkedIn profile has no API: save_profile_update writes
ready-to-paste text and the candidate updates the profile manually.
"""

from __future__ import annotations

import csv
import html
import os
import webbrowser
from pathlib import Path
from urllib.parse import quote

import httpx

from jobs_helper import policy as pol
from jobs_helper.linkedin.oauth import load_token
from jobs_helper.models import now_iso
from jobs_helper.tracker import workspace

API = "https://api.linkedin.com"
LINKEDIN_VERSION = os.environ.get("LINKEDIN_VERSION", "202609")
POST_MAX = 3000
HEADLINE_MAX, ABOUT_MAX, POSITION_MAX = 220, 2600, 2000
WRITER_ROLES = {"reviewer", "single_agent", "manual"}
POST_FIELDS = ["post_id", "created_at", "status", "policy", "visibility", "text", "submitted_by"]


def err(code: str, message: str, hint: str = "", retryable: bool = False, **extra) -> dict:
    return {"error": code, "message": message, "hint": hint, "retryable": retryable, **extra}


class LinkedInService:
    def __init__(self, client: httpx.Client | None = None, role: str | None = None, confirmer=None, opener=None):
        self.client = client or httpx.Client(timeout=30)
        self.role = role or os.environ.get("AGENT_ROLE", "manual")
        self.confirmer = confirmer or preview_confirm
        self.opener = opener or webbrowser.open

    # ------------------------------------------------------------ helpers
    def _token(self) -> tuple[str | None, dict | None]:
        tok = load_token()
        if not tok:
            return None, err("not_connected", "LinkedIn is not connected.", "Ask the candidate to run `uv run python -m jobs_helper login linkedin`.")
        if tok["expired"]:
            return None, err("token_expired", "The LinkedIn token has expired.", "Ask the candidate to run `uv run python -m jobs_helper login linkedin` again.")
        return tok["access_token"], None

    def _record(self, **row) -> None:
        path = workspace() / "linkedin_posts.csv"
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=POST_FIELDS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in POST_FIELDS} | {"created_at": now_iso(), "submitted_by": self.role})

    # ------------------------------------------------------------ get_profile
    def get_profile(self) -> dict:
        token, e = self._token()
        if e:
            return e
        r = self.client.get(f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 401:
            return err("token_expired", "LinkedIn rejected the token.", "Run `uv run python -m jobs_helper login linkedin`.")
        if r.status_code >= 400:
            return err("linkedin_error", f"userinfo failed: HTTP {r.status_code}", "Retry later.", True)
        u = r.json()
        return {"name": u.get("name"), "given_name": u.get("given_name"), "family_name": u.get("family_name"),
                "email": u.get("email"), "picture": u.get("picture"), "locale": u.get("locale"),
                "note": "The LinkedIn API exposes only name, photo and email; headline / about / experience are not readable or writable."}

    # ------------------------------------------------------------ publish_post
    def publish_post(self, text: str, visibility: str = "PUBLIC") -> dict:
        if self.role not in WRITER_ROLES:
            return err("forbidden", f"Role '{self.role}' may not publish.", "Only the reviewer agent publishes; hand the draft over.")
        text = (text or "").strip()
        if not text:
            return err("invalid_argument", "Post text is empty.", "Write the post first.", True)
        if len(text) > POST_MAX:
            return err("post_too_long", f"Post has {len(text)} chars, LinkedIn allows {POST_MAX}.", "Shorten it and retry.", True)
        if visibility not in ("PUBLIC", "CONNECTIONS"):
            return err("invalid_argument", f"visibility={visibility!r}.", "Use PUBLIC or CONNECTIONS.", True)
        policy = pol.load_policies()["linkedin"]
        if pol.dry_run():
            self._record(status="prepared", policy="dry_run", visibility=visibility, text=text)
            return {"ok": True, "status": "prepared", "policy": "dry_run", "message": "Draft saved (dry run): nothing was published."}
        note = ""
        if policy in ("confirm", "auto") and not load_token():
            # no LinkedIn app / token (typical for non-technical users): the share dialog needs no API at all
            policy, note = "manual", " LinkedIn API is not connected, so the share dialog was used instead."
        if policy == "manual":
            self.opener("https://www.linkedin.com/feed/?shareActive=true&text=" + quote(text))
            self._record(status="awaiting_user", policy=policy, visibility=visibility, text=text)
            return {"ok": True, "status": "awaiting_user", "policy": policy,
                    "message": "LinkedIn's share dialog was opened with the text; the candidate publishes it." + note}
        token, e = self._token()
        if e:
            return e
        if policy == "confirm" and not self.confirmer(text, visibility):
            self._record(status="declined", policy=policy, visibility=visibility, text=text)
            return {"ok": True, "status": "declined", "policy": policy, "message": "The candidate cancelled the post."}
        me = self.client.get(f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
        if me.status_code >= 400:
            return err("token_expired" if me.status_code == 401 else "linkedin_error", f"userinfo failed: HTTP {me.status_code}",
                       "Run `uv run python -m jobs_helper login linkedin`.")
        body = {"author": f"urn:li:person:{me.json()['sub']}", "commentary": text, "visibility": visibility,
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        r = self.client.post(f"{API}/rest/posts", json=body, headers={
            "Authorization": f"Bearer {token}", "LinkedIn-Version": LINKEDIN_VERSION,
            "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"})
        if r.status_code == 429:
            return err("rate_limited", "LinkedIn rate limit reached.", "Try again later.", True)
        if r.status_code >= 400:
            return err("linkedin_error", f"Posting failed: HTTP {r.status_code} {r.text[:200]}", "Check the token scopes (w_member_social).")
        post_id = r.headers.get("x-restli-id", "")
        self._record(post_id=post_id, status="published", policy=policy, visibility=visibility, text=text)
        return {"ok": True, "status": "published", "policy": policy, "post_id": post_id,
                "url": f"https://www.linkedin.com/feed/update/{post_id}/" if post_id else None}

    # ------------------------------------------------------------ save_profile_update
    def save_profile_update(self, headline: str, about: str, skills: list[str], experience_bullets: list[str] | None = None,
                            positions: list[dict] | None = None, approved_by_candidate: bool = False) -> dict:
        """Ready-to-paste LinkedIn texts. Draft first; after the candidate approves, a copy-helper page opens next to
        their profile. LinkedIn has no API to edit a profile and its rules forbid automating it — the human pastes."""
        if self.role not in WRITER_ROLES:
            return err("forbidden", f"Role '{self.role}' may not save profile updates.", "Only the reviewer agent writes.")
        headline, about = (headline or "").strip(), (about or "").strip()
        if not headline or not about:
            return err("invalid_argument", "headline and about are required.", "Provide both.", True)
        if len(headline) > HEADLINE_MAX:
            return err("headline_too_long", f"Headline has {len(headline)} chars, LinkedIn allows {HEADLINE_MAX}.", "Shorten it.", True)
        if len(about) > ABOUT_MAX:
            return err("about_too_long", f"About has {len(about)} chars, LinkedIn allows {ABOUT_MAX}.", "Shorten it.", True)
        positions = positions or []
        for p in positions:
            if len(p.get("description", "")) > POSITION_MAX:
                return err("position_too_long", f"Description of '{p.get('title')}' exceeds {POSITION_MAX} chars.", "Shorten it.", True)
        skills = [x.strip() for x in skills if x and x.strip()]
        warnings = []
        if len(skills) > 50:
            warnings.append(f"{len(skills)} skills: LinkedIn shows the first ones; keep the 20-30 most relevant.")
        unverified = _unverified("\n".join([headline, about, *skills, *(experience_bullets or []),
                                             *[p.get("description", "") for p in positions]]))
        if unverified["numbers"] or unverified["technologies"]:
            warnings.append("Contains facts not found in the candidate's CV / documents — confirm them with the candidate.")
        ws = workspace()
        md_path, html_path = ws / "profile_update.md", ws / "profile_update.html"
        lines = ["# Оновлення профілю LinkedIn", "", f"_Підготовлено {now_iso()[:10]}._", "",
                 "## Заголовок (Headline)", "", headline, "", "## Про себе (About)", "", about, "",
                 "## Навички (Skills)", "", ", ".join(skills)]
        for p in positions:
            lines += ["", f"## Досвід: {p.get('title', '')} — {p.get('company', '')} ({p.get('dates', '')})", "", p.get("description", "")]
        if experience_bullets:
            lines += ["", "## Пункти досвіду", ""] + [f"- {b.strip()}" for b in experience_bullets if b.strip()]
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        html_path.write_text(_helper_html(headline, about, skills, positions, experience_bullets or []), encoding="utf-8")
        out = {"ok": True, "path": str(md_path), "helper_page": str(html_path), "unverified": unverified, "warnings": warnings,
               "preview": {"headline": headline, "about": about[:400] + ("…" if len(about) > 400 else ""), "skills": skills[:30],
                           "positions": [f"{p.get('title')} — {p.get('company')}" for p in positions]}}
        if not approved_by_candidate:
            out.update(status="draft_saved", message="Show this to the candidate. Only after they approve, call again with "
                                                     "approved_by_candidate=true to open the copy-helper next to their profile.")
            return out
        if pol.dry_run():
            out.update(status="prepared", message="Dry run: the helper page was saved but not opened.")
            return out
        self.opener(html_path.as_uri())
        self.opener("https://www.linkedin.com/in/me/")
        out.update(status="opened_for_candidate",
                   message="Opened the copy-helper and the candidate's LinkedIn profile: they paste each block themselves.")
        return out


def _unverified(text: str) -> dict:
    try:
        from jobs_helper.cv.review import unverified_claims
        sources = [pol.read_cv_text(f) for f in pol.candidate_files()]
        if pol.master_cv_path().exists():
            sources.append(pol.master_cv_path().read_text(encoding="utf-8"))
        sources += [n["text"] for n in pol.list_notes()]
        return unverified_claims(text, "\n".join(sources)) if sources else {"numbers": [], "technologies": []}
    except Exception:  # noqa: BLE001
        return {"numbers": [], "technologies": []}


def _helper_html(headline: str, about: str, skills: list[str], positions: list[dict], bullets: list[str]) -> str:
    def block(title: str, where: str, text: str) -> str:
        bid = f"b{abs(hash(title)) % 10**8}"
        return (f"<section><h2>{html.escape(title)}</h2><p class='where'>{html.escape(where)}</p>"
                f"<textarea id='{bid}' readonly>{html.escape(text)}</textarea>"
                f"<button onclick=\"navigator.clipboard.writeText(document.getElementById('{bid}').value);"
                f"this.textContent='Скопійовано ✓'\">Копіювати</button><span class='n'>{len(text)} символів</span></section>")
    parts = [block("Заголовок (Headline)", "Профіль → ✏️ біля імені → Заголовок", headline),
             block("Про себе (About)", "Профіль → розділ «Про себе» → ✏️", about),
             block("Навички (Skills)", "Профіль → «Навички» → + (додавайте по одній)", "\n".join(skills))]
    for p in positions:
        parts.append(block(f"{p.get('title', '')} — {p.get('company', '')}", f"Профіль → «Досвід» → ✏️ біля посади ({p.get('dates', '')}) → Опис",
                           p.get("description", "")))
    if bullets:
        parts.append(block("Пункти досвіду", "Для опису посад", "\n".join(f"• {b}" for b in bullets)))
    return f"""<!doctype html><html lang="uk"><head><meta charset="utf-8"><title>Оновлення профілю LinkedIn</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
:root {{ --bg:#f4f6f8; --fg:#1d232b; --card:#fff; --muted:#5b6673; --accent:#0a66c2; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#11151a; --fg:#e6e9ee; --card:#1a2028; --muted:#9aa5b1; --accent:#5aa9ff; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; max-width:820px; margin:24px auto; padding:0 16px; }}
section {{ background:var(--card); border-radius:12px; padding:14px 16px; margin:14px 0; }}
h1 {{ font-size:22px; }} h2 {{ font-size:16px; margin:0 0 2px; }} .where {{ color:var(--muted); margin:0 0 8px; font-size:13px; }}
textarea {{ width:100%; min-height:90px; box-sizing:border-box; border:1px solid #c9d1da; border-radius:8px; padding:8px; font:inherit;
            background:transparent; color:inherit; }}
button {{ margin-top:8px; background:var(--accent); color:#fff; border:0; border-radius:8px; padding:7px 14px; cursor:pointer; font-weight:600; }}
.n {{ color:var(--muted); font-size:12px; margin-left:10px; }} a {{ color:var(--accent); }}
</style></head><body><h1>Оновлення профілю LinkedIn</h1>
<p>Скопіюйте блок і вставте у відповідне поле. <a href="https://www.linkedin.com/in/me/" target="_blank">Відкрити мій профіль →</a></p>
{''.join(parts)}</body></html>"""


def preview_confirm(text: str, visibility: str) -> bool:
    """Shows the post in a small browser window with Publish / Cancel; returns the candidate's choice."""
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
    page_html = f"""<!doctype html><html><head><meta charset="utf-8"><title>LinkedIn post preview</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:32px auto;padding:0 16px;color:#111}}
.card{{border:1px solid #ddd;border-radius:10px;padding:16px;white-space:pre-wrap}}
button{{margin:16px 8px 0 0;padding:10px 18px;border:0;border-radius:8px;font-weight:600;cursor:pointer;color:#fff}}</style></head>
<body><h2>Опублікувати в LinkedIn ({visibility})?</h2><div class="card">{html.escape(text)}</div>
<button style="background:#0a66c2" onclick="window.__jh_decision='yes'">Опублікувати</button>
<button style="background:#64748b" onclick="window.__jh_decision='no'">Скасувати</button></body></html>"""
    from jobs_helper.apply.browser_env import ensure_chromium
    ensure_chromium()
    timeout = int(os.environ.get("JOBS_HUMAN_TIMEOUT", "900")) * 1000
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.set_content(page_html)
        try:
            page.wait_for_function("() => window.__jh_decision", timeout=timeout)
            return page.evaluate("() => window.__jh_decision") == "yes"
        except PWTimeout:
            return False
        finally:
            browser.close()
