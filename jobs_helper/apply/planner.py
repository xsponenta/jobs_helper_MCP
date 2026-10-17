"""Decides what goes into every field of an application form. Shared by all apply backends.

Sources of values, in order: documents (CV / cover letter) -> candidate.yaml -> answers from the agent.
Sensitive fields (work authorization, visa, consent, criminal record) accept candidate.yaml only:
an agent answer is ignored and the question is marked human_only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from jobs_helper.apply.fields import FormField, autofill_allowed, candidate_values

SOURCE_LABELS = {"dou": "DOU", "djinni": "Djinni", "happymonday": "Happy Monday"}


@dataclass
class FillPlan:
    values: dict[str, str] = field(default_factory=dict)       # field id -> value
    origin: dict[str, str] = field(default_factory=dict)       # field label -> where the value came from
    questions: list[dict] = field(default_factory=list)        # unanswered required fields

    @property
    def complete(self) -> bool:
        return not self.questions


def plan_fill(fields: list[FormField], profile: dict, answers: dict[str, str], job_source: str,
              cv_text: str = "", cover_text: str = "") -> FillPlan:
    """File fields get a placeholder (the backend uploads the PDF); text fields get the real text."""
    vals = candidate_values(profile)
    answers = {k.strip(): str(v).strip() for k, v in (answers or {}).items() if str(v).strip()}
    plan = FillPlan()
    for f in fields:
        key = f.key
        value, origin = None, None
        if key == "resume":
            value, origin = ("<cv.pdf>" if f.type == "file" else cv_text or "<cv.pdf>"), "cv"
        elif key == "cover_letter":
            value, origin = ("<cover_letter.pdf>" if f.type == "file" else cover_text or "<cover_letter.pdf>"), "cover_letter"
        elif key in vals and (autofill_allowed(key, f.label) or f.sensitive):
            value, origin = vals[key], "candidate.yaml"
        elif key == "how_did_you_hear" and job_source in SOURCE_LABELS:
            value, origin = SOURCE_LABELS[job_source], "job source"
        elif not f.sensitive and (saved := _saved_answer(f.label, profile.get("saved_answers") or {})):
            value, origin = saved, "saved answer"
        elif not f.sensitive:
            ans = answers.get(f.id) or answers.get(f.label)
            if ans:
                value, origin = ans, "agent answer"
        if value is not None:
            plan.values[f.id] = value
            plan.origin[f.label] = origin
        elif f.required:
            plan.questions.append({
                "field_id": f.id, "label": f.label, "type": f.type, "options": f.options,
                "human_only": f.sensitive,
            })
    return plan


def _norm(text: str) -> str:
    return re.sub(r"[^\w ]+", " ", text.lower()).replace("_", " ").split("  ")[0].strip()


def _saved_answer(label: str, saved: dict[str, str]) -> str | None:
    """An answer the candidate gave earlier for (almost) the same question."""
    lab = " ".join(_norm(label).split())
    best, best_score = None, 0.0
    for question, answer in saved.items():
        q = " ".join(_norm(question).split())
        score = SequenceMatcher(None, lab, q).ratio()
        if q and (q in lab or lab in q):
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = answer, score
    return best if best_score >= 0.75 else None
