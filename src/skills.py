"""
skills.py
=========
Reusable **Agent Skills** for the Smart HR Recruitment system.

A "skill" here is a self-contained, testable capability that an ADK agent can
invoke as a tool. The same Python callables are:

  * wrapped as ``google.adk.tools.FunctionTool`` for LLM agents, and
  * called directly by the deterministic fallback orchestrator, so the whole
    pipeline still runs end-to-end on Kaggle even without a Gemini API key.

Skills implemented:
  * resume_parser_skill   - extract structured candidate profile from raw text
  * jd_parser_skill       - extract structured role requirements from a JD
  * candidate_scorer_skill- score a candidate profile against role requirements
  * email_drafter_skill   - draft a personalized interview invitation email
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config

# --------------------------------------------------------------------------- #
# Skill taxonomy: a controlled vocabulary of skills + aliases. Using a
# normalized vocabulary makes scoring robust to surface variations
# ("k8s" == "kubernetes", "postgres" == "postgresql").
# --------------------------------------------------------------------------- #
SKILL_VOCAB: Dict[str, List[str]] = {
    "python": ["python", "fastapi", "django", "flask", "pandas", "numpy"],
    "go": ["golang", "go"],
    "java": ["java"],
    "javascript": ["javascript", "typescript", "node.js", "nodejs", "react", "express"],
    "sql": ["sql", "postgresql", "postgres", "mysql"],
    "postgresql": ["postgresql", "postgres"],
    "nosql": ["mongodb", "redis", "dynamodb", "cassandra"],
    "gcp": ["gcp", "google cloud", "cloud run", "bigquery", "gke", "pub/sub"],
    "aws": ["aws", "ec2", "lambda", "ecs", "eks", "s3", "rds", "dynamodb", "sqs"],
    "azure": ["azure"],
    "docker": ["docker", "container"],
    "kubernetes": ["kubernetes", "k8s", "gke", "eks"],
    "terraform": ["terraform", "iac", "infrastructure-as-code", "infrastructure as code"],
    "microservices": ["microservice", "distributed system"],
    "rest": ["rest", "restful", "rest api"],
    "grpc": ["grpc", "protocol buffer", "protobuf"],
    "messaging": ["kafka", "rabbitmq", "pub/sub", "pubsub", "sqs", "message queue"],
    "ci/cd": ["ci/cd", "cicd", "github actions", "jenkins", "gitlab ci"],
    "observability": ["prometheus", "grafana", "observability", "monitoring"],
    "r": ["r"],
    "c": ["c"],
    "excel": ["excel"],
    "word": ["word"],
}

_DEGREE_RE = re.compile(
    r"\b(ph\.?d|m\.?tech|m\.?sc|m\.?s|mba|b\.?tech|b\.?e|b\.?sc|b\.?s|bachelor|master|doctor)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4}")

_AMBIGUOUS_SKILLS = {"go", "r", "c", "excel", "word"}
_PROGRAMMING_CONTEXT_RE = re.compile(
    r"(?:knowledge of|experience (?:with|in)|proficiency in|familiarity with|"
    r"programming(?: language)?|developer|engineer|language)\b[^\n]{0,60}$",
    re.IGNORECASE,
)


def _alias_pattern(alias: str) -> str:
    """Whole-token alias pattern; allows a simple plural suffix only."""
    escaped = re.escape(alias.strip())
    if escaped and escaped[-1].isalnum():
        return rf"(?<![a-z0-9]){escaped}s?(?![a-z0-9])"
    return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"


def _contains_alias(text: str, alias: str) -> bool:
    return bool(re.search(_alias_pattern(alias), text, re.IGNORECASE))


def _ambiguous_present(canonical: str, text: str, skills_text: str) -> bool:
    """Ambiguous words (go/r/c/excel/word) need list or programming context."""
    aliases = SKILL_VOCAB[canonical]
    if canonical == "go" and _contains_alias(text, "golang"):
        return True

    # A dedicated skills list is the strongest possible context.
    if any(_contains_alias(skills_text, alias) for alias in aliases):
        return True

    # Bullet/list entries ("- Go", "- R", "- Microsoft Excel") are list context.
    for alias in aliases:
        if re.search(rf"(?m)^\s*[-*•]\s*{_alias_pattern(alias)}\s*$", text, re.IGNORECASE):
            return True

    if canonical in {"excel", "word"}:
        esc = re.escape(canonical)
        return bool(re.search(
            rf"(?<![a-z0-9])(?:microsoft|ms)\s+{esc}s?(?![a-z0-9])",
            text,
            re.IGNORECASE,
        ))

    for alias in aliases:
        for match in re.finditer(_alias_pattern(alias), text, re.IGNORECASE):
            prefix = text[max(0, match.start() - 80):match.start()]
            if _PROGRAMMING_CONTEXT_RE.search(prefix):
                return True
    return False


def _detect_skills(text: str) -> List[str]:
    """Return canonical skills present in ``text`` using whole-token matching."""
    low = text.lower()
    skills_text = _split_sections(text).get("skills", "").lower()
    found = []
    for canonical, aliases in SKILL_VOCAB.items():
        if canonical in _AMBIGUOUS_SKILLS:
            if _ambiguous_present(canonical, low, skills_text):
                found.append(canonical)
        elif any(_contains_alias(low, alias) for alias in aliases):
            found.append(canonical)
    return sorted(set(found))


_YEAR_RANGE_RE = re.compile(
    r"\b((?:19|20)\d{2})\s*(?:-|–|—|to|until)\s*"
    r"(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\.?,?\s*)?"
    r"((?:19|20)\d{2}|present|current|now)\b",
    re.IGNORECASE,
)
_EXPLICIT_YEARS_RE = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_EDU_LINE_RE = re.compile(
    r"\b(university|college|bachelors?|bsc|b\.sc|msc|m\.sc|mba|ph\.?d|matric|"
    r"intermediate|fsc|cgpa|gpa)\b",
    re.IGNORECASE,
)


def _without_education(text: str) -> str:
    """Drop education blocks/headings so degree dates are not counted as jobs."""
    kept: List[str] = []
    in_education = False
    for line in text.splitlines():
        heading = _section_heading(line)
        if heading:
            in_education = heading == "education"
            continue
        if in_education or _EDU_LINE_RE.search(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def estimate_experience_years(text: str) -> float:
    """Estimate professional experience, merging overlapping dated roles."""
    if not text:
        return 0.0

    working_text = _without_education(text)
    explicit = [
        float(value)
        for value in _EXPLICIT_YEARS_RE.findall(working_text)
        if float(value) <= 45
    ]

    this_year = datetime.now().year
    spans = []
    for start_raw, end_raw in _YEAR_RANGE_RE.findall(working_text):
        start = int(start_raw)
        end = this_year if not end_raw.isdigit() else int(end_raw)
        if start <= end <= this_year + 1 and end - start <= 40:
            spans.append((start, end))

    spans.sort()
    total = 0.0
    current = None
    for start, end in spans:
        if current is None:
            current = [start, end]
        elif start <= current[1]:
            current[1] = max(current[1], end)
        else:
            total += max(current[1] - current[0], 0)
            current = [start, end]
    if current is not None:
        total += max(current[1] - current[0], 0)

    return round(min(max(max(explicit, default=0.0), total), 40.0), 1)


def _estimate_years(text: str) -> int:
    """Backward-compatible integer wrapper around ``estimate_experience_years``."""
    return int(round(estimate_experience_years(text)))


_SECTION_ALIASES = {
    "summary": {"summary", "profile", "objective", "about", "professional summary"},
    "skills": {
        "skills", "keywords", "core competencies", "technical skills",
        "technologies", "tech stack", "competencies",
    },
    "experience": {
        "experience", "work experience", "professional experience",
        "employment", "work history", "employment history",
    },
    "education": {"education", "academic background", "academics"},
}
_SECTION_LOOKUP = {
    alias: canonical
    for canonical, aliases in _SECTION_ALIASES.items()
    for alias in aliases
}


def _section_heading(line: str) -> str:
    """Return the canonical section name if ``line`` is a heading, else ''."""
    cleaned = re.sub(r"[^a-z ]", "", line.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return _SECTION_LOOKUP.get(cleaned, "")


def _split_sections(text: str) -> Dict[str, str]:
    """Split a resume into summary/skills/experience/education blocks."""
    blocks: Dict[str, List[str]] = {}
    current = "header"
    for line in text.splitlines():
        heading = _section_heading(line)
        if heading:
            current = heading
            blocks.setdefault(current, [])
            continue
        blocks.setdefault(current, []).append(line)
    return {name: "\n".join(lines).strip() for name, lines in blocks.items()}


# --------------------------------------------------------------------------- #
# SKILL 1: resume_parser_skill
# --------------------------------------------------------------------------- #
def resume_parser_skill(resume_text: str, source_id: str = "") -> Dict[str, Any]:
    """Parse a raw resume into a structured candidate profile.

    Args:
        resume_text: Full plain-text contents of a resume.
        source_id: Identifier (e.g., filename) for traceability.

    Returns:
        A dict with name, contact, skills, experience_years, education.
    """
    lines = [ln.strip() for ln in resume_text.splitlines() if ln.strip()]
    name = lines[0].title() if lines else "Unknown Candidate"
    parse_status = "ok" if len(resume_text.strip()) >= 20 else "unreadable"

    email_m = _EMAIL_RE.search(resume_text)
    phone_m = _PHONE_RE.search(resume_text.replace(email_m.group() if email_m else "", ""))

    degrees = sorted({d.upper().replace(".", "") for d in _DEGREE_RE.findall(resume_text)})
    sections = _split_sections(resume_text)
    # Evidence is strongest in the experience block. If the resume has no
    # experience heading, fall back to the whole document rather than treating
    # an unheaded resume as having zero evidence.
    experience_text = sections.get("experience") or resume_text

    return {
        "source_id": source_id,
        "name": name,
        "email": email_m.group() if email_m else "",
        "phone": phone_m.group().strip() if phone_m else "",
        "skills": _detect_skills(resume_text),
        "experienced_skills": _detect_skills(experience_text),
        "experience_years": estimate_experience_years(resume_text),
        "education": degrees,
        "parse_status": parse_status,
        "parse_warnings": [] if parse_status == "ok" else [
            "No extractable text (image-only or empty PDF)"
        ],
    }


# --------------------------------------------------------------------------- #
# SKILL 2: jd_parser_skill
# --------------------------------------------------------------------------- #
_JD_REQUIRED_HEADINGS = {
    "required", "required skills", "required qualifications", "requirements",
    "must have", "must haves", "must-have", "minimum qualifications",
    "minimum requirements", "essential skills", "essential criteria",
}
_JD_NICE_HEADINGS = {
    "nice to have", "nice-to-have", "nice to haves", "preferred",
    "preferred qualifications", "preferred skills", "bonus", "bonus points",
    "good to have", "desirable", "desirable skills", "plus", "a plus",
}
_JD_OTHER_HEADINGS = {
    "about the role", "about us", "responsibilities", "key responsibilities",
    "what you will do", "what you'll do", "qualifications", "employment type",
    "experience level", "benefits", "who you are", "the role",
}
_ANY_OF_RE = re.compile(
    r"\b(?:at least one|any one of|any of|one of|one or more)\b", re.IGNORECASE
)


def _normalized_heading(line: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", line.lower())).strip()


def _jd_sections(text: str) -> Dict[str, str]:
    blocks: Dict[str, List[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        heading = _normalized_heading(line)
        if heading in _JD_REQUIRED_HEADINGS:
            current = "required"
        elif heading in _JD_NICE_HEADINGS:
            current = "nice"
        elif heading in _JD_OTHER_HEADINGS or (
            line.rstrip().endswith(":") and 0 < len(heading.split()) <= 6
        ):
            current = "other"
        else:
            blocks.setdefault(current, []).append(line)
    return {name: "\n".join(lines).strip() for name, lines in blocks.items()}


def _any_of_skill_groups(text: str) -> List[List[str]]:
    groups: List[List[str]] = []
    for line in text.splitlines():
        if not _ANY_OF_RE.search(line):
            continue
        options = _detect_skills(line)
        if len(options) >= 2 and options not in groups:
            groups.append(sorted(options))
    return groups


def _jd_min_years(text: str) -> int:
    values = [
        int(value)
        for value in re.findall(
            r"\b(\d{1,2})(?:\s*[-–—]\s*\d{1,2})?\+?\s*(?:years?|yrs?)\b",
            text,
            re.IGNORECASE,
        )
    ]
    return max(values, default=0)


def jd_parser_skill(jd_text: str) -> Dict[str, Any]:
    """Extract structured requirements from a job description.

    Required skills come only from an explicit Required/Requirements section;
    Nice-to-have skills come only from Nice/Preferred/Bonus sections. Lines such
    as "at least one cloud platform (GCP/AWS/Azure)" become an any-of group.
    """
    low = jd_text.lower()

    title_m = re.search(r"job title:\s*(.+)", jd_text, re.IGNORECASE)
    title = title_m.group(1).strip() if title_m else (jd_text.splitlines()[0].strip() if jd_text else "Role")

    sections = _jd_sections(jd_text)
    required_region = sections.get("required", "")
    if not required_region.strip():
        required_region = sections.get("header", "") or jd_text
    nice_region = sections.get("nice", "")

    required_groups = _any_of_skill_groups(required_region)
    grouped_skills = {skill for group in required_groups for skill in group}

    required_skills = [
        skill for skill in _detect_skills(required_region)
        if skill not in grouped_skills
    ]
    nice_to_have = [
        skill for skill in _detect_skills(nice_region)
        if skill not in required_skills and skill not in grouped_skills
    ]

    min_years = _jd_min_years(jd_text)
    seniority = "senior" if ("senior" in low or min_years >= 5) else (
        "mid" if min_years >= 2 else "junior"
    )
    degrees = sorted({d.upper().replace(".", "") for d in _DEGREE_RE.findall(jd_text)})

    return {
        "title": title,
        "required_skills": required_skills,
        "required_skill_groups": required_groups,
        "nice_to_have": nice_to_have,
        "min_years": min_years,
        "seniority": seniority,
        "qualifications": degrees,
    }


# --------------------------------------------------------------------------- #
# SKILL 3: candidate_scorer_skill
# --------------------------------------------------------------------------- #
NICE_TO_HAVE_WEIGHT = 0.10


def recommendation_for_score(
    match: float,
    keyword_stuffing_suspected: bool = False,
    shortlistable: bool = True,
) -> str:
    """Map a score to the shared shortlist/review/reject recommendation."""
    if not shortlistable:
        return "Unreadable resume - manual review"
    if keyword_stuffing_suspected:
        if match >= config.REVIEW_THRESHOLD:
            return "Possible match - review (keyword stuffing suspected)"
        return "Weak match - likely reject (keyword stuffing suspected)"
    if match >= config.SHORTLIST_THRESHOLD:
        return "Strong match - shortlist"
    if match >= config.REVIEW_THRESHOLD:
        return "Possible match - review"
    return "Weak match - likely reject"


def candidate_scorer_skill(
    candidate: Dict[str, Any],
    requirements: Dict[str, Any],
) -> Dict[str, Any]:
    """Score a parsed candidate against parsed JD requirements.

    Weighted model:
        * 50% required-skill coverage (half listed, half demonstrated in experience)
        * 10% nice-to-have coverage
        * 25% experience match
        *  5% education match
        * 10% evidence coverage (required skills found in the experience block)

    The evidence split is what stops a resume that merely lists every keyword
    from outranking a candidate who can show the same skills in real roles.
    Returns a dict with match_percent (0-100) and an explainable breakdown.
    """
    req_skills = set(requirements.get("required_skills", []))
    required_groups = [
        set(group) for group in requirements.get("required_skill_groups", [])
        if group
    ]
    nice_skills = set(requirements.get("nice_to_have", []))
    cand_skills = set(candidate.get("skills", []))
    experienced_skills = set(candidate.get("experienced_skills", []))

    matched_groups = [sorted(group & cand_skills) for group in required_groups
                      if group & cand_skills]
    missing_groups = [sorted(group) for group in required_groups
                      if not group & cand_skills]
    group_matches = {skill for hit in matched_groups for skill in hit}
    group_experience = {
        skill for group in required_groups
        for skill in (group & experienced_skills)
    }

    matched_req = sorted((req_skills & cand_skills) | group_matches)
    matched_req_experience = sorted((req_skills & experienced_skills) | group_experience)
    missing_req = sorted(req_skills - cand_skills)
    matched_nice = sorted(nice_skills & cand_skills)

    total_required_units = len(req_skills) + len(required_groups)
    listed_hits = len(req_skills & cand_skills) + len(matched_groups)
    evidence_hits = len(req_skills & experienced_skills) + sum(
        1 for group in required_groups if group & experienced_skills
    )
    listed_cov = listed_hits / total_required_units if total_required_units else 1.0
    evidence_cov = evidence_hits / total_required_units if total_required_units else 1.0
    skill_score = 0.5 * listed_cov + 0.5 * evidence_cov
    nice_cov = len(matched_nice) / len(nice_skills) if nice_skills else 0.0

    min_years = requirements.get("min_years", 0) or 0
    cand_years = candidate.get("experience_years", 0) or 0
    exp_score = 1.0 if min_years == 0 else min(cand_years / min_years, 1.0)

    edu_score = 1.0 if candidate.get("education") else 0.0

    match = 100.0 * (
        0.50 * skill_score
        + NICE_TO_HAVE_WEIGHT * nice_cov
        + 0.25 * exp_score
        + 0.05 * edu_score
        + 0.10 * evidence_cov
    )
    match = round(match, 1)

    parse_status = candidate.get("parse_status", "ok")
    shortlistable = parse_status != "unreadable"
    matched_units = len(req_skills & cand_skills) + len(matched_groups)
    evidence_ratio = (
        evidence_hits / matched_units if matched_units else 0.0
    )
    keyword_stuffing_suspected = (
        len(cand_skills) >= 12
        and listed_cov >= 0.8
        and evidence_cov < 0.35
    )
    recommendation = recommendation_for_score(
        match,
        keyword_stuffing_suspected=keyword_stuffing_suspected,
        shortlistable=shortlistable,
    )

    return {
        "name": candidate.get("name"),
        "source_id": candidate.get("source_id"),
        "match_percent": match,
        "matched_required": matched_req,
        "matched_required_in_experience": matched_req_experience,
        "matched_required_groups": matched_groups,
        "missing_required": missing_req,
        "missing_required_groups": missing_groups,
        "matched_nice_to_have": matched_nice,
        "experience_years": cand_years,
        "listed_skill_coverage_percent": round(100 * listed_cov, 1),
        "evidence_coverage_percent": round(100 * evidence_cov, 1),
        "evidence_ratio_percent": round(100 * evidence_ratio, 1),
        "keyword_stuffing_suspected": keyword_stuffing_suspected,
        "parse_status": parse_status,
        "parse_warnings": candidate.get("parse_warnings", []),
        "shortlistable": shortlistable,
        "recommendation": recommendation,
        "needs_human_review": keyword_stuffing_suspected or not shortlistable,
        "confidence": 0.0 if not shortlistable else round(
            0.55 + 0.45 * evidence_cov, 3) if total_required_units else 0.8,
        "memory_hint": None,
    }


# --------------------------------------------------------------------------- #
# SKILL 4: email_drafter_skill
# --------------------------------------------------------------------------- #
def email_drafter_skill(
    candidate: Dict[str, Any],
    role_title: str,
    company: str = "NimbusPay Technologies",
    recruiter_name: str = "Alex Morgan",
    slot: str = "",
) -> Dict[str, Any]:
    """Draft a personalized interview invitation email for a candidate.

    Returns a dict with to, subject, and body fields.
    """
    name = candidate.get("name", "Candidate")
    first = name.split()[0] if name else "there"
    strengths = candidate.get("matched_required", []) or candidate.get("skills", [])
    strengths_str = ", ".join(strengths[:3]) if strengths else "your background"

    subject = f"Interview Invitation - {role_title} at {company}"
    body = (
        f"Dear {first},\n\n"
        f"Thank you for applying for the {role_title} position at {company}. "
        f"After reviewing your application, we were impressed by your experience "
        f"with {strengths_str} and would like to invite you to an interview.\n\n"
        f"Proposed slot: {slot}\n"
        f"Format: 45-minute video call with the Platform Engineering team.\n\n"
        f"Please reply to confirm whether this time works, or suggest an "
        f"alternative that suits you better.\n\n"
        f"We look forward to speaking with you.\n\n"
        f"Best regards,\n{recruiter_name}\nTalent Acquisition, {company}"
    )
    return {
        "to": candidate.get("email", ""),
        "candidate": name,
        "subject": subject,
        "body": body,
        "slot": slot,
    }


def suggest_interview_slot_datetimes(
    n: int,
    start_in_days: int = 3,
    timezone: str | None = None,
    hours: List[int] | None = None,
) -> List[datetime]:
    """Suggest ``n`` business-hour interview slots as timezone-aware datetimes."""
    if n <= 0:
        return []

    tz_name = timezone or config.INTERVIEW_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    slots: List[datetime] = []
    day = datetime.now(tz) + timedelta(days=max(0, start_in_days))
    for_hours = hours or [10, 14, 16]
    while len(slots) < n:
        if day.weekday() < 5:
            for hour in for_hours:
                if len(slots) >= n:
                    break
                slot = day.replace(hour=hour, minute=0, second=0, microsecond=0)
                slots.append(slot)
        day += timedelta(days=1)
    return slots


def format_interview_slot(slot: datetime) -> str:
    """Format an interview datetime for the invitation email."""
    return slot.strftime("%A, %d %b %Y at %H:%M %Z")


def suggest_interview_slots(
    n: int,
    start_in_days: int = 3,
    timezone: str | None = None,
) -> List[str]:
    """Suggest ``n`` business-hour interview slots in the configured timezone."""
    return [
        format_interview_slot(slot)
        for slot in suggest_interview_slot_datetimes(n, start_in_days, timezone)
    ]


__all__ = [
    "SKILL_VOCAB",
    "NICE_TO_HAVE_WEIGHT",
    "resume_parser_skill",
    "jd_parser_skill",
    "candidate_scorer_skill",
    "recommendation_for_score",
    "email_drafter_skill",
    "suggest_interview_slot_datetimes",
    "suggest_interview_slots",
    "format_interview_slot",
    "estimate_experience_years",
]
