"""Maps form field labels (UA / EN, as seen on DOU, Djinni and ATS forms) to candidate profile keys.

The same matcher is used by the mock backend and, later, by the browser backend."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# canonical key -> label regex. Order matters: first match wins.
FIELD_PATTERNS: list[tuple[str, str]] = [
    ("resume", r"resume|\bcv\b|резюме"),
    ("cover_letter", r"cover letter|супровідн|motivation letter|повідомлення|message|розкажіть"),
    # contacts before names: 'Ім'я користувача Telegram' is a Telegram handle, not a first name
    ("email", r"e-?mail|пошта"),
    ("phone", r"phone|телефон|mobile"),
    ("linkedin", r"linkedin"),
    ("github", r"github"),
    ("telegram", r"telegram|телеграм"),
    ("full_name", r"full name|your name|ваше ім.я|повне ім.я|ім.я (та|і) прізвище|прізвище,? (та |і )?ім.я|піб|^name\b"),
    ("first_name", r"first name|^ім.я\s*[*✱]?\s*$"),
    ("last_name", r"last name|прізвище|surname"),
    ("portfolio", r"portfolio|website|blog|other website|сайт|портфоліо"),
    ("city", r"current location|location|city|місто|локаці|місцезнаходження|розташування|місце проживання|where are you (located|based)"),
    ("country", r"^country|країна"),
    ("current_company", r"current company|поточна компанія|current employer"),
    ("desired_salary", r"salary|compensation|rate|зарплат|компенсац|financial expectations|очікування"),
    ("notice_period", r"notice period|start date|available from|коли можете приступити|термін виходу"),
    ("english", r"english|англійськ"),
    ("how_did_you_hear", r"how did you hear|where did you (hear|find)|звідки ви дізнались|job boards"),
    # sensitive: answered only from candidate.yaml or by the human
    ("work_authorization", r"authori[sz]ed to work|work permit|right to work|дозвіл на роботу"),
    ("visa", r"visa|sponsorship|віза"),
    ("consent", r"consent|agree|privacy|gdpr|згод|обробку персональних|personal data"),
    ("criminal_record", r"criminal|судимість"),
]
SENSITIVE = {"work_authorization", "visa", "consent", "criminal_record"}


_SALARY_QUALIFIERS = re.compile(r"hour|annual|year|річн|погодин|aed|eur|€|pln|gbp|£|gross|net\b|b2b|total", re.I)
_USD = re.compile(r"usd|\$|долар", re.I)


def autofill_allowed(key: str | None, label: str) -> bool:
    """Profile values may be auto-filled only when their meaning matches the question exactly.
    A monthly USD salary must not answer 'hourly rate (EUR, gross)'."""
    if key is None or key in SENSITIVE:
        return False
    if key == "desired_salary" and (_SALARY_QUALIFIERS.search(label) or not _USD.search(label)):
        return False   # the profile holds monthly USD; anything else (or an unknown currency) is a question
    return True


def match_field(label: str, input_type: str | None = None) -> str | None:
    if input_type == "file":
        low = label.lower()
        return "cover_letter" if re.search(r"cover", low) else "resume"
    low = re.sub(r"\s+", " ", label.lower()).strip()
    if input_type == "checkbox":   # a checkbox is never a document; "agree to keep my CV" is consent
        if re.search(r"agree|consent|declare|privacy|gdpr|згод|погоджу|personal data|обробку", low):
            return "consent"
    for key, pat in FIELD_PATTERNS:
        if input_type == "checkbox" and key in ("resume", "cover_letter"):
            continue
        if re.search(pat, low):
            return key
    return None


def candidate_values(profile: dict) -> dict[str, str]:
    """Flattens candidate.yaml into canonical keys. Empty values are dropped."""
    p, links = profile.get("personal", {}), profile.get("links", {})
    pref, lang = profile.get("job_preferences", {}), profile.get("languages", {})
    sens, ans = profile.get("sensitive", {}), profile.get("answers", {})
    first, last = p.get("first_name", ""), p.get("last_name", "")
    values = {
        "first_name": first, "last_name": last, "full_name": f"{first} {last}".strip(),
        "email": p.get("email"), "phone": p.get("phone"), "city": p.get("city"), "country": p.get("country"),
        "linkedin": links.get("linkedin"), "github": links.get("github"), "portfolio": links.get("portfolio"),
        "telegram": links.get("telegram"), "current_company": p.get("current_company"),
        "desired_salary": str(pref["desired_salary_usd"]) if pref.get("desired_salary_usd") else None,
        "notice_period": pref.get("notice_period"), "english": lang.get("english"),
        "how_did_you_hear": ans.get("how_did_you_hear"),
        "work_authorization": _fmt(sens.get("work_authorization")),
        "visa": _fmt(sens.get("requires_visa_sponsorship")),
        "consent": _fmt(ans.get("consent_to_processing")),
        "criminal_record": _fmt(sens.get("criminal_record")),
    }
    return {k: str(v) for k, v in values.items() if v not in (None, "", {}, [])}


def _fmt(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {'yes' if x else 'no'}" for k, x in v.items()) or None
    return v


@dataclass
class FormField:
    id: str
    label: str
    type: str = "text"            # text | textarea | file | select | radio | checkbox | email | tel
    required: bool = False
    options: list[str] = field(default_factory=list)

    @property
    def key(self) -> str | None:
        return match_field(self.label, self.type)

    @property
    def sensitive(self) -> bool:
        return self.key in SENSITIVE
