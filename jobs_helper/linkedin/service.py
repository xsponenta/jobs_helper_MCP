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
HEADLINE_MAX, ABOUT_MAX = 220, 2600
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
        with path.open("a", newline="") as f:
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
        if policy == "manual":
            self.opener("https://www.linkedin.com/feed/?shareActive=true&text=" + quote(text))
            self._record(status="awaiting_user", policy=policy, visibility=visibility, text=text)
            return {"ok": True, "status": "awaiting_user", "policy": policy,
                    "message": "LinkedIn's share dialog was opened with the text; the candidate publishes it."}
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
    def save_profile_update(self, headline: str, about: str, skills: list[str], experience_bullets: list[str] | None = None) -> dict:
        if self.role not in WRITER_ROLES:
            return err("forbidden", f"Role '{self.role}' may not save profile updates.", "Only the reviewer agent writes.")
        headline, about = (headline or "").strip(), (about or "").strip()
        if not headline or not about:
            return err("invalid_argument", "headline and about are required.", "Provide both.", True)
        if len(headline) > HEADLINE_MAX:
            return err("headline_too_long", f"Headline has {len(headline)} chars, LinkedIn allows {HEADLINE_MAX}.", "Shorten it.", True)
        if len(about) > ABOUT_MAX:
            return err("about_too_long", f"About has {len(about)} chars, LinkedIn allows {ABOUT_MAX}.", "Shorten it.", True)
        path = workspace() / "profile_update.md"
        lines = ["# Оновлення профілю LinkedIn", "", f"_Підготовлено {now_iso()[:10]}. Вставте вручну: "
                 "LinkedIn → профіль → ✏️ (заголовок), «Про себе», «Навички»._", "",
                 "## Заголовок (Headline)", "", headline, "", "## Про себе (About)", "", about, "",
                 "## Навички (Skills)", "", ", ".join(s.strip() for s in skills if s.strip())]
        if experience_bullets:
            lines += ["", "## Пункти досвіду", ""] + [f"- {b.strip()}" for b in experience_bullets if b.strip()]
        path.write_text("\n".join(lines) + "\n")
        return {"ok": True, "status": "saved", "path": str(path),
                "message": "Profile text saved; the candidate pastes it into LinkedIn (the API cannot edit profiles)."}


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
