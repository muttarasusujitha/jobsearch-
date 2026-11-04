"""Rule-based job matching. No AI models: keyword rules, title similarity and regex.

Score out of 100:
  title similarity to your roles   up to 40
  nice-to-have keywords found      up to 30
  location match                   15
  experience fits                  15
Hard rules (exclusions, missing must-haves, too much experience required) skip the job.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .models import JobPosting

REMOTE_WORDS = ("remote", "work from home", "wfh", "anywhere")
STOP_WORDS = {"and", "or", "the", "of", "a", "an", "-", "/", "&", "i", "ii", "iii"}

# "3+ years", "2-5 yrs", "3 to 5 years", "minimum 4 years", "at least 2 years"
EXPERIENCE_PATTERNS = [
    re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I),
    re.compile(r"(?:minimum|min\.?|at least|atleast)\s*(?:of\s*)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I),
    re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.I),
    re.compile(r"(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s*)?(?:relevant\s*|professional\s*|industry\s*)?(?:experience|exp)", re.I),
]


@dataclass
class MatchResult:
    score: float
    passed: bool
    reasons: list[str] = field(default_factory=list)


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9+#.\s]", " ", text.lower())


def _tokens(text: str) -> set[str]:
    return {t.strip(".") for t in _normalize(text).split() if t.strip(".") and t not in STOP_WORDS}


def contains_keyword(text: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


def title_similarity(title: str, role: str) -> float:
    """0..1 similarity between a job title and one of your target roles."""
    t, r = _tokens(title), _tokens(role)
    if not t or not r:
        return 0.0
    coverage = len(t & r) / len(r)  # how much of your role appears in the title
    sequence = SequenceMatcher(None, _normalize(title), _normalize(role)).ratio()
    return max(coverage, sequence)


def required_experience(text: str) -> int | None:
    """Smallest 'minimum years' requirement found in the text, or None."""
    found: list[int] = []
    for pattern in EXPERIENCE_PATTERNS:
        for m in pattern.finditer(text):
            years = int(m.group(1))
            if years <= 20:
                found.append(years)
    return min(found) if found else None


def location_matches(job_location: str, description: str, wanted: list[str]) -> bool:
    if not wanted:
        return True
    loc = (job_location or "").lower()
    head = description[:500].lower()
    for place in wanted:
        p = place.lower().strip()
        if p == "remote":
            if any(w in loc for w in REMOTE_WORDS) or (not loc and any(w in head for w in REMOTE_WORDS)):
                return True
        elif p and (p in loc or (p == "bangalore" and "bengaluru" in loc) or (p == "bengaluru" and "bangalore" in loc)):
            return True
    return False


def score_job(job: JobPosting, prefs: dict[str, Any]) -> MatchResult:
    title = job.title or ""
    description = clean_text(job.description)
    full_text = f"{title} {job.company} {description}"
    reasons: list[str] = []

    company = (job.company or "").lower()
    for c in prefs.get("exclude_companies", []):
        if c.lower() and c.lower() in company:
            return MatchResult(0, False, [f"Excluded company: {c}"])

    for kw in prefs.get("exclude_title_keywords", []):
        if contains_keyword(title, kw):
            return MatchResult(0, False, [f"Excluded title keyword: {kw}"])

    for kw in prefs.get("exclude_keywords", []):
        if contains_keyword(full_text, kw):
            return MatchResult(0, False, [f"Excluded keyword: {kw}"])

    roles = prefs.get("roles", [])
    best_role, best_sim = "", 0.0
    for role in roles:
        sim = title_similarity(title, role)
        if sim > best_sim:
            best_role, best_sim = role, sim
    if best_sim < 0.5:
        return MatchResult(round(best_sim * 40, 1), False, [f"Title doesn't match your roles (best: {best_role or 'none'})"])
    score = best_sim * 40
    reasons.append(f"Title ~ '{best_role}' ({best_sim:.0%})")

    # Short LinkedIn/Naukri search cards have no description yet. Only enforce
    # keyword rules when we actually have text to check.
    has_description = len(description) > 200
    must = prefs.get("must_have_keywords", [])
    if has_description:
        missing = [kw for kw in must if not contains_keyword(full_text, kw)]
        if missing:
            return MatchResult(round(score, 1), False, reasons + [f"Missing must-have: {', '.join(missing)}"])

    nice = prefs.get("nice_to_have_keywords", [])
    if nice:
        hits = [kw for kw in nice if contains_keyword(full_text, kw)]
        if has_description:
            score += 30 * len(hits) / len(nice)
        else:
            score += 15  # neutral when there's no description to check
        if hits:
            reasons.append("Skills: " + ", ".join(hits))
    else:
        score += 30

    wanted_locations = prefs.get("locations", [])
    if location_matches(job.location, description, wanted_locations):
        score += 15
        if wanted_locations:
            reasons.append(f"Location OK ({job.location or 'n/a'})")
    elif prefs.get("strict_location", False):
        return MatchResult(round(score, 1), False, reasons + [f"Location not wanted: {job.location}"])
    else:
        reasons.append(f"Location mismatch ({job.location})")

    my_years = prefs.get("experience_years")
    needed = required_experience(f"{title} {description}")
    if my_years is None or needed is None:
        score += 10
    else:
        tolerance = prefs.get("experience_tolerance", 1)
        if needed > my_years + tolerance:
            return MatchResult(round(score, 1), False, reasons + [f"Needs {needed}+ years (you have {my_years})"])
        score += 15
        reasons.append(f"Experience OK (asks {needed}+)")

    for c in prefs.get("preferred_companies", []):
        if c and contains_keyword(job.company or "", c):
            score += 10
            reasons.append(f"Preferred company: {c}")
            break

    score = round(min(score, 100), 1)
    min_score = prefs.get("min_score", 50)
    if score < min_score:
        return MatchResult(score, False, reasons + [f"Score below {min_score}"])
    return MatchResult(score, True, reasons)
