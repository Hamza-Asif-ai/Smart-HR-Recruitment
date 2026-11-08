"""Detect which role an application email is for, and match it to the searched role.

The role is read from the email *subject* first (e.g. ``Application for AI
Engineer Position - Name``). Only when the subject names no role is the opening
of the body used. Matching is whole-word and order-independent, so ``AI
Engineer`` matches ``Junior AI Engineer`` but never ``Backend Software
Engineer`` (the old substring check let ``ai`` match inside ``email``).
"""

from __future__ import annotations

import re
from typing import Iterable, Set, Tuple

# Seniority / filler words that must not decide whether two roles are the same.
STOPWORDS = {
    "position", "role", "job", "title", "the", "a", "an", "of", "for", "and", "at", "in", "to",
    "we", "are", "is", "hiring", "looking", "opening", "vacancy", "senior", "junior", "lead",
    "sr", "jr", "i", "ii", "iii", "iv", "intern", "internship", "opportunity", "fresh",
    "entry", "level", "trainee", "associate", "post", "application", "applying",
}

_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|aw|sv)\s*:\s*)+", re.I)
_ARTICLE = r"(?:the\s+|a\s+|an\s+)?"
_ROLE_NOUN = r"(?:position|role|post|vacancy|opening|job|opportunity)"
_END = r"(?:\s+[-–—|:(]\s*|\s*[-–—|(]\s+|\s*[,;]\s*|$)"

_SUBJECT_PATTERNS = (
    # "Application for the Senior AI Engineer position - Name"
    re.compile(r"\b(?:application|applying|apply|applicant)\s+(?:for|to)\s*:?\s*" + _ARTICLE
               + r"(.+?)\s+" + _ROLE_NOUN + r"\b", re.I),
    # "Application for AI Engineer - Name" / "Applying for: AI Engineer"
    re.compile(r"\b(?:application|applying|apply|applicant)\s+(?:for|to)\s*:?\s*" + _ARTICLE
               + r"(.+?)" + _END, re.I),
    # "Job Application - AI Engineer", "Resume: AI Engineer", "CV - AI Engineer"
    re.compile(r"\b(?:job\s+application|application|resume|cv|curriculum\s+vitae)\s*[-–—:|]\s*"
               r"(.+?)(?:\s+[-–—|(]\s+|\s*[,;]\s*|$)", re.I),
    # "AI Engineer Position - Name"
    re.compile(r"^\s*" + _ARTICLE + r"(.+?)\s+(?:position|role)\b", re.I),
)

_BODY_PATTERN = re.compile(
    r"\b(?:apply(?:ing)?\s+(?:for|to)|interest\s+in|application\s+for|candidate\s+for|"
    r"candidacy\s+for|applying\s+for)\s+" + _ARTICLE
    + r"([A-Za-z0-9/&+.\- ]{2,60}?)\s+" + _ROLE_NOUN + r"\b", re.I)


def role_tokens(text: str) -> Set[str]:
    """Whole-word tokens that identify a role (ai == artificial intelligence, ml == machine learning)."""
    t = (text or "").lower()
    t = re.sub(r"\bartificial\s+intelligence\b", "ai", t)
    t = re.sub(r"\bmachine\s+learning\b", "ml", t)
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in STOPWORDS and len(w) > 1}


def _clean(role: str) -> str:
    role = re.sub(r"\s+", " ", role or "").strip(" .:-–—|,;()\"'")
    role = re.sub(r"\b(?:position|role|post|vacancy|opening|job)\s*$", "", role, flags=re.I).strip()
    return role[:80] if role_tokens(role) else ""


def detect_applied_role(subject: str, body: str = "") -> Tuple[str, str]:
    """Return ``(role, source)``; source is ``subject``, ``body`` or ``""`` when nothing is found."""
    subj = _SUBJECT_PREFIX.sub("", subject or "").strip()
    for pattern in _SUBJECT_PATTERNS:
        m = pattern.search(subj)
        if m:
            role = _clean(m.group(1))
            if role:
                return role, "subject"
    head = " ".join((body or "")[:1500].split())
    m = _BODY_PATTERN.search(head)
    if m:
        role = _clean(m.group(1))
        if role:
            return role, "body"
    return "", ""


def role_matches(wanted: str, subject: str, body: str = "") -> Tuple[bool, str]:
    """True when the application is for the searched role. Returns ``(matches, detected_role)``.

    Every word of the searched role must appear (as a whole word) in the detected role,
    so extra words are fine ("Software Engineer" matches "Backend Software Engineer").
    If no role can be detected, fall back to whole-word matching over subject + body.
    """
    wanted_tokens = role_tokens(wanted)
    if not wanted_tokens:
        return True, detect_applied_role(subject, body)[0]
    role, _source = detect_applied_role(subject, body)
    if role:
        return wanted_tokens <= role_tokens(role), role
    return wanted_tokens <= role_tokens(f"{subject} {body}"), ""


def summarize_roles(roles: Iterable[str], limit: int = 6) -> str:
    """'Software Engineer (4), AI Engineer (2)' for the "nothing matched" hint."""
    counts = {}
    for role in roles:
        key = (role or "").strip() or "role not stated"
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:limit]
    return ", ".join(f"{name} ({n})" for name, n in ranked)
