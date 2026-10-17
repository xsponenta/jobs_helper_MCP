"""Mock apply backend: realistic form schemas (taken from the stage-1 recon) without touching real sites.
Used by tests and evaluation, so results never depend on page markup or send real applications.

Env:
  JOBS_MOCK_SESSIONS  sites with a logged-in session, comma separated (default: dou,djinni,happymonday)
  JOBS_MOCK_CONFIRM   answer of the simulated human for the 'confirm' policy: yes | no (default yes)
  JOBS_MOCK_CAPTCHA   targets that show a captcha on submit, comma separated (default: none)
"""

from __future__ import annotations

import os

from jobs_helper.apply.backend import ApplyOutcome, ApplyRequest
from jobs_helper.apply.fields import FormField as F
from jobs_helper.apply.planner import plan_fill

FORMS: dict[str, list[F]] = {
    "djinni": [F("message", "Повідомлення", "textarea"), F("email", "Електронна пошта", "email", True),
               F("name", "Ваше ім'я"), F("linkedin", "LinkedIn профіль", "text", True), F("cv", "Резюме", "file")],
    "dou": [F("cover", "Супровідний лист", "textarea", True), F("cv", "Резюме", "file", True)],
    "happymonday": [F("cover", "Супровідний лист", "textarea"), F("cv", "Резюме", "file", True)],
    "ats:greenhouse": [
        F("first_name", "First Name*", "text", True), F("last_name", "Last Name*", "text", True),
        F("email", "Email*", "email", True), F("country", "Country*", "text", True), F("phone", "Phone*", "tel", True),
        F("resume", "Resume/CV", "file", True), F("cover", "Cover Letter", "file"),
        F("linkedin", "Link to LinkedIn page*", "textarea", True), F("english", "Level of English*", "text", True),
        F("location", "Your current location:*", "text", True),
        F("q_rate", "What is your expected hourly rate (USD Gross) for a B2B contract?*", "text", True),
        F("consent", "By submitting this application, you agree that we may save your data*", "checkbox", True)],
    "ats:lever": [
        F("resume", "Resume/CV", "file", True), F("name", "Full name", "text", True), F("email", "Email", "email", True),
        F("phone", "Phone", "tel", True), F("location", "Current location", "text", True),
        F("org", "Current company"), F("linkedin", "LinkedIn URL"), F("site", "Other website")],
    "ats:ashby": [
        F("name", "Name", "text", True), F("email", "Email", "email", True), F("phone", "Phone", "tel", True),
        F("resume", "Resume", "file", True), F("location", "Location"), F("cover", "Cover Letter", "textarea"),
        F("linkedin", "LinkedIn", "url"),
        F("source", "Job Boards (DOU, djinni, Glassdoor, Indeed, etc.)", "radio"),
        F("q_why", "Why are you looking for a new position?", "textarea", True),
        F("q_achievements", "Can you describe your most meaningful achievements?", "textarea", True)],
    "ats:bamboohr": [
        F("first", "First Name *", "text", True), F("last", "Last Name *", "text", True), F("email", "Email *", "email", True),
        F("phone", "Phone *", "tel", True), F("resume", "Resume", "file", True), F("site", "Website, Blog or Portfolio"),
        F("linkedin", "LinkedIn URL"),
        F("q_salary", "What are your financial expectations (total monthly compensation in AED)? *", "text", True),
        F("q_notice", "What is your current notice period? *", "text", True),
        F("q_visa", "What is your visa type, status, duration? *", "text", True),
        F("q_location", "What is your current location? (city, country) *", "text", True)],
    "ats:peopleforce": [
        F("name", "Full name", "text", True), F("email", "Email", "email", True), F("phone", "Phone", "tel"),
        F("resume", "Resume", "file", True), F("telegram", "Telegram username"),
        F("salary", "Desired base compensation"), F("location", "Location")],
}
GENERIC = [F("name", "Full name", "text", True), F("email", "Email", "email", True), F("resume", "Resume", "file", True),
           F("cover", "Cover letter", "textarea")]
LOGIN_REQUIRED = {"dou", "happymonday"}


def _env_set(name: str, default: str) -> set[str]:
    return {x.strip() for x in os.environ.get(name, default).split(",") if x.strip()}


class MockBackend:
    def apply(self, req: ApplyRequest) -> ApplyOutcome:
        if req.target in LOGIN_REQUIRED and req.target not in _env_set("JOBS_MOCK_SESSIONS", "dou,djinni,happymonday"):
            return ApplyOutcome("failed", error={
                "error": "not_logged_in", "message": f"No saved {req.target} session.",
                "hint": f"Ask the candidate to run `uv run python -m jobs_helper login {req.target}`.", "retryable": False})

        fields = FORMS.get(req.target, GENERIC)
        plan = plan_fill(fields, req.profile, req.answers, req.job.source, req.cv_markdown, req.cover_letter)
        if not plan.complete:
            return ApplyOutcome("needs_input", filled=plan.origin, questions=plan.questions,
                                message=f"{len(plan.questions)} required field(s) need an answer.")

        if req.dry_run:
            return ApplyOutcome("prepared", filled=plan.origin, message="Form filled (dry run): nothing was sent.")
        if req.target in _env_set("JOBS_MOCK_CAPTCHA", ""):
            return ApplyOutcome("failed", filled=plan.origin, error={
                "error": "captcha_required", "message": "The form shows a captcha on submit.",
                "hint": "Hand the prepared form to the candidate to finish manually.", "retryable": False})
        if req.policy == "manual":
            return ApplyOutcome("awaiting_user", filled=plan.origin,
                                message="Form filled and left open; the candidate presses 'Submit' themselves.")
        if req.policy == "confirm":
            if os.environ.get("JOBS_MOCK_CONFIRM", "yes").lower() not in ("yes", "y", "1", "true"):
                return ApplyOutcome("declined", filled=plan.origin, message="The candidate declined this submission.")
        return ApplyOutcome("submitted", filled=plan.origin, message="Application submitted.")
