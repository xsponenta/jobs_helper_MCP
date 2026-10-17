"""Apply backends share one contract: take a prepared request, fill the form, act on the policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from jobs_helper.models import Job


@dataclass
class ApplyRequest:
    job: Job
    target: str                 # see jobs_helper.ats.apply_target
    profile: dict               # candidate.yaml
    cv_markdown: str
    cover_letter: str
    answers: dict[str, str]
    policy: str                 # manual | confirm | auto
    dry_run: bool


@dataclass
class ApplyOutcome:
    status: str                 # prepared | awaiting_user | submitted | declined | needs_input | failed
    filled: dict[str, str] = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    screenshot: str | None = None
    message: str = ""
    error: dict | None = None   # structured error for the agent (code/message/hint/retryable)


class ApplyBackend(Protocol):
    def apply(self, req: ApplyRequest) -> ApplyOutcome: ...


class Confirmer(Protocol):
    """Asks the human whether to press 'Submit'. The browser backend shows it inside the page."""

    def confirm(self, req: ApplyRequest, filled: dict[str, str]) -> bool: ...
