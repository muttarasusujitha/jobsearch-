"""Answers application form questions using rules and your profile. No AI.

If no rule matches a required question, `answer()` returns None and the application
is moved to "Needs attention" instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable

from .config import Settings

YES = ("yes", "y", "true", "i am", "i do", "i will", "i agree", "agree", "accept", "i accept")
NO = ("no", "n", "false", "i am not", "i do not", "i don't", "i will not")
# Returned by a rule that recognised the question but has no answer for it.
UNKNOWN = object()
DECLINE = ("decline", "prefer not", "not wish", "don't wish", "do not wish", "rather not", "not to say", "not to disclose")


@dataclass
class Question:
    label: str
    kind: str = "text"  # text | number | textarea | select | radio | checkbox | file
    options: list[str] | None = None
    required: bool = False


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_cover_letter(settings: Settings, title: str, company: str) -> str:
    template = settings.cover_letter_template
    if not template:
        return ""
    values = _SafeDict(
        first_name=settings.personal.get("first_name", ""),
        last_name=settings.personal.get("last_name", ""),
        current_title=settings.personal.get("current_title", ""),
        experience_years=settings.preferences.get("experience_years", ""),
        title=title,
        company=company or "your",
    )
    return template.format_map(values).strip()


def _num(text: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


def _range_contains(option: str, value: float) -> bool:
    text = option.lower().replace(",", "")
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return False
    if len(nums) >= 2:
        return nums[0] <= value <= nums[1]
    n = nums[0]
    if any(w in text for w in ("+", "more", "above", "over", "greater", "at least")):
        return value >= n
    if any(w in text for w in ("less", "under", "below", "upto", "up to", "within", "or fewer")):
        return value <= n
    return value == n


def choose_option(options: list[str], value: Any) -> str | None:
    """Pick the option that best represents `value`."""
    opts = [o for o in options if o and o.strip() and not o.lower().startswith(("select", "choose", "--"))]
    if not opts:
        return None
    if isinstance(value, bool):
        words = YES if value else NO
        for o in opts:
            if o.strip().lower() in words:
                return o
        for o in opts:
            if o.strip().lower().startswith(words):
                return o
        return None
    if value == "__decline__":
        for o in opts:
            if any(d in o.lower() for d in DECLINE):
                return o
        return None
    if isinstance(value, (int, float)):
        for o in opts:
            if _range_contains(o, float(value)):
                return o
        if value == 0:
            for o in opts:
                if any(w in o.lower() for w in ("immediate", "none", "fresher", "no experience")):
                    return o
        return None
    text = str(value).lower().strip()
    for o in opts:
        if o.lower().strip() == text:
            return o
    best, best_ratio = None, 0.0
    for o in opts:
        ol = o.lower()
        ratio = 1.0 if (text in ol or ol in text) else SequenceMatcher(None, text, ol).ratio()
        if ratio > best_ratio:
            best, best_ratio = o, ratio
    return best if best_ratio >= 0.6 else None


class Answerer:
    def __init__(self, settings: Settings, cover_letter: str = ""):
        self.s = settings
        self.p = settings.personal
        self.a = settings.answers
        self.prefs = settings.preferences
        self.cover_letter = cover_letter
        self.custom = [
            (re.compile(c["pattern"], re.I), c["answer"])
            for c in self.a.get("custom", [])
            if isinstance(c, dict) and c.get("pattern")
        ]
        # (pattern, handler(question, match) -> value). Order matters: first match wins.
        self.rules: list[tuple[re.Pattern[str], Callable[[Question, re.Match[str]], Any]]] = [
            (re.compile(r"cover\s*letter|why .*(join|interested|apply|want to work)|message to (the )?hiring", re.I),
             lambda q, m: self.cover_letter or None),
            (re.compile(r"\b(gender|sex|race|ethnic|veteran|disabilit|sexual orientation|pronoun)", re.I),
             lambda q, m: "__decline__" if self.a.get("decline_eeo_questions", True) else None),
            (re.compile(r"first\s*name|given name", re.I), lambda q, m: self.p.get("first_name")),
            (re.compile(r"last\s*name|surname|family name", re.I), lambda q, m: self.p.get("last_name")),
            (re.compile(r"^\s*(full\s*)?name\s*\*?\s*$|your name|full name", re.I),
             lambda q, m: f"{self.p.get('first_name', '')} {self.p.get('last_name', '')}".strip()),
            (re.compile(r"e-?mail", re.I), lambda q, m: self.p.get("email")),
            (re.compile(r"phone|mobile|contact number|whatsapp", re.I), lambda q, m: self._phone(q)),
            (re.compile(r"linkedin", re.I), lambda q, m: self.p.get("linkedin_url") or None),
            (re.compile(r"github", re.I), lambda q, m: self.p.get("github_url") or None),
            (re.compile(r"portfolio|website|personal (site|url)", re.I), lambda q, m: self.p.get("portfolio_url") or None),
            (re.compile(r"notice period|how soon|when can you (start|join)|joining time|availability to join", re.I),
             lambda q, m: self._number_or_text(q, self.a.get("notice_period_days"), suffix=" days")),
            (re.compile(r"expected (ctc|salary|compensation|pay)|salary expectation|desired (salary|compensation)", re.I),
             lambda q, m: self._money(q, "expected")),
            (re.compile(r"current (ctc|salary|compensation|pay)|present (ctc|salary)|last drawn", re.I),
             lambda q, m: self._money(q, "current")),
            (re.compile(r"years?\b.{0,40}?\b(experience|exp|worked|working)\b.{0,25}?\b(with|in|using|on)\s+(?P<skill>[\w#+.\- ]{1,40})", re.I),
             self._skill_years),
            (re.compile(r"(total|overall|relevant|years of|how many years|work) .{0,20}experience|experience \(in years\)|years of experience", re.I),
             lambda q, m: self._number_or_text(q, self.prefs.get("experience_years"))),
            (re.compile(r"sponsor|visa", re.I), lambda q, m: bool(self.a.get("requires_sponsorship", False))),
            (re.compile(r"authori[sz]ed|legally (eligible|allowed|permitted)|right to work|eligible to work", re.I),
             lambda q, m: bool(self.a.get("authorized_to_work", True))),
            (re.compile(r"relocat", re.I), lambda q, m: bool(self.a.get("willing_to_relocate", True))),
            (re.compile(r"on-?site|in-?office|from (the )?office|hybrid|commute", re.I),
             lambda q, m: bool(self.a.get("willing_to_work_onsite", True))),
            (re.compile(r"how did you (hear|find|learn)|source|referred by|where did you", re.I),
             lambda q, m: self.a.get("how_did_you_hear")),
            (re.compile(r"highest (level of )?(education|qualification|degree)|education level|degree", re.I),
             lambda q, m: self.a.get("highest_education")),
            (re.compile(r"current (company|employer|organi[sz]ation)|present employer|employer", re.I),
             lambda q, m: self.p.get("current_company")),
            (re.compile(r"current (title|role|designation|position)|job title|designation", re.I),
             lambda q, m: self.p.get("current_title")),
            (re.compile(r"\b(city|location|where are you based|current location|address)\b", re.I),
             lambda q, m: self.p.get("city")),
            (re.compile(r"country", re.I), lambda q, m: self.p.get("country")),
            (re.compile(r"privacy|consent|terms|acknowledge|i (certify|confirm|agree)|accurate", re.I),
             lambda q, m: True),
        ]

    # ---- helpers -------------------------------------------------------
    def _phone(self, q: Question) -> str | None:
        phone = str(self.p.get("phone", ""))
        if q.kind == "select":  # country code dropdowns
            return None
        if "country code" in q.label.lower():
            m = re.match(r"\+\d{1,3}", phone)
            return m.group() if m else None
        return phone

    def _number_or_text(self, q: Question, value: Any, suffix: str = "") -> Any:
        if value is None:
            return None
        if q.kind in ("select", "radio"):
            return value  # numeric value, matched against ranges by choose_option
        if q.kind == "number":
            return str(value)
        return f"{value}{suffix}" if suffix else str(value)

    def _money(self, q: Question, which: str) -> Any:
        number = self.a.get(f"{which}_ctc_number")
        text = self.a.get(f"{which}_ctc")
        if q.kind == "number":
            if number is not None:
                return str(number)
            parsed = _num(text) if text else None
            return str(int(parsed)) if parsed is not None else None
        if q.kind in ("select", "radio"):
            if number is None:
                return text
            # Options are often in lakhs ("5-10 LPA") rather than rupees.
            options_text = " ".join(q.options or []).lower()
            if "lakh" in options_text or "lpa" in options_text:
                return number / 100000
            return number
        return text

    def _skill_years(self, q: Question, m: re.Match[str]) -> Any:
        skill = (m.group("skill") or "").strip(" ?.:*").lower()
        if re.match(r"(total|overall|the industry|industry|this field|software|it\b|a similar|similar|this role|professional)", skill):
            return self._number_or_text(q, self.prefs.get("experience_years"))
        table = {str(k).lower(): v for k, v in self.a.get("skill_years", {}).items()}
        years = None
        for name, value in table.items():
            if re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", skill):
                years = value
                break
        if years is None:
            years = self.a.get("unknown_skill_years")
        if years is None:
            return UNKNOWN  # don't fall through to the "total experience" rule
        return self._number_or_text(q, years)

    # ---- public --------------------------------------------------------
    def answer(self, q: Question) -> str | None:
        """Return the value to type/select, 'yes'/'no' for checkboxes, or None if unknown."""
        label = re.sub(r"\s+", " ", q.label or "").strip()
        if not label:
            return None
        value: Any = None
        for pattern, ans in self.custom:
            if pattern.search(label):
                value = ans
                break
        else:
            for pattern, handler in self.rules:
                m = pattern.search(label)
                if m:
                    value = handler(q, m)
                    if value is not None:
                        break
        if value is None or value is UNKNOWN:
            return None

        if q.kind in ("select", "radio") and q.options:
            return choose_option(q.options, value)
        if q.kind == "checkbox":
            if isinstance(value, bool):
                return "yes" if value else "no"
            return "yes" if str(value).lower() in YES else None
        if value == "__decline__":
            return None
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)
