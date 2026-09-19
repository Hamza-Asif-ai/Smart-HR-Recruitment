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

# --------------------------------------------------------------------------- #
# Skill taxonomy: a controlled vocabulary of skills + aliases. Using a
# normalized vocabulary makes scoring robust to surface variations
# ("k8s" == "kubernetes", "postgres" == "postgresql").
# --------------------------------------------------------------------------- #
SKILL_VOCAB: Dict[str, List[str]] = {
    "python": ["python", "fastapi", "django", "flask", "pandas", "numpy"],
    "go": ["golang", "go "],
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
}

_DEGREE_RE = re.compile(
    r"\b(ph\.?d|m\.?tech|m\.?sc|m\.?s|mba|b\.?tech|b\.?e|b\.?sc|b\.?s|bachelor|master|doctor)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4}")
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*years", re.IGNORECASE)


def _detect_skills(text: str) -> List[str]:
    """Return the canonical skills present in ``text`` using the vocabulary."""
    low = text.lower()
    found = []
    for canonical, aliases in SKILL_VOCAB.items():
        if any(alias in low for alias in aliases):
            found.append(canonical)
    return sorted(set(found))


def _estimate_years(text: str) -> int:
    """Best-effort estimate of years of experience from free text."""
    matches = [int(m) for m in _YEARS_RE.findall(text)]
    if matches:
        return max(matches)
    # Fall back to scanning year ranges like "2017 - Present".
    years = [int(y) for y in re.findall(r"(19|20)\d{2}", text)]
    if len(years) >= 2:
        span = datetime.now().year - min(years)
        return max(0, min(span, 40))
    return 0


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

    email_m = _EMAIL_RE.search(resume_text)
    phone_m = _PHONE_RE.search(resume_text.replace(email_m.group() if email_m else "", ""))

    degrees = sorted({d.upper().replace(".", "") for d in _DEGREE_RE.findall(resume_text)})

    return {
        "source_id": source_id,
        "name": name,
        "email": email_m.group() if email_m else "",
        "phone": phone_m.group().strip() if phone_m else "",
        "skills": _detect_skills(resume_text),
        "experience_years": _estimate_years(resume_text),
        "education": degrees,
    }


# --------------------------------------------------------------------------- #
# SKILL 2: jd_parser_skill
# --------------------------------------------------------------------------- #
def jd_parser_skill(jd_text: str) -> Dict[str, Any]:
    """Extract structured requirements from a job description.

    Returns a dict with title, required_skills, nice_to_have, min_years,
    seniority, and qualifications.
    """
    low = jd_text.lower()

    title_m = re.search(r"job title:\s*(.+)", jd_text, re.IGNORECASE)
    title = title_m.group(1).strip() if title_m else (jd_text.splitlines()[0].strip() if jd_text else "Role")

    # Split into "required" vs "nice to have" regions for weighting.
    nice_idx = low.find("nice to have")
    required_region = low[:nice_idx] if nice_idx != -1 else low
    nice_region = low[nice_idx:] if nice_idx != -1 else ""

    required_skills = _detect_skills(required_region)
    nice_to_have = [s for s in _detect_skills(nice_region) if s not in required_skills]

    min_years = _estimate_years(jd_text)
    seniority = "senior" if ("senior" in low or min_years >= 5) else (
        "mid" if min_years >= 2 else "junior"
    )
    degrees = sorted({d.upper().replace(".", "") for d in _DEGREE_RE.findall(jd_text)})

    return {
        "title": title,
        "required_skills": required_skills,
        "nice_to_have": nice_to_have,
        "min_years": min_years,
        "seniority": seniority,
        "qualifications": degrees,
    }


# --------------------------------------------------------------------------- #
# SKILL 3: candidate_scorer_skill
# --------------------------------------------------------------------------- #
def candidate_scorer_skill(
    candidate: Dict[str, Any],
    requirements: Dict[str, Any],
) -> Dict[str, Any]:
    """Score a parsed candidate against parsed JD requirements.

    Weighted model:
        * 60% required-skill coverage
        * 15% nice-to-have coverage
        * 20% experience match
        *  5% education match
    Returns a dict with match_percent (0-100) and an explainable breakdown.
    """
    req_skills = set(requirements.get("required_skills", []))
    nice_skills = set(requirements.get("nice_to_have", []))
    cand_skills = set(candidate.get("skills", []))

    matched_req = sorted(req_skills & cand_skills)
    missing_req = sorted(req_skills - cand_skills)
    matched_nice = sorted(nice_skills & cand_skills)

    req_cov = len(matched_req) / len(req_skills) if req_skills else 1.0
    nice_cov = len(matched_nice) / len(nice_skills) if nice_skills else 0.0

    min_years = requirements.get("min_years", 0) or 0
    cand_years = candidate.get("experience_years", 0) or 0
    exp_score = 1.0 if min_years == 0 else min(cand_years / min_years, 1.0)

    edu_score = 1.0 if candidate.get("education") else 0.0

    match = 100.0 * (0.60 * req_cov + 0.15 * nice_cov + 0.20 * exp_score + 0.05 * edu_score)
    match = round(match, 1)

    if match >= 75:
        recommendation = "Strong match - shortlist"
    elif match >= 55:
        recommendation = "Possible match - review"
    else:
        recommendation = "Weak match - likely reject"

    return {
        "name": candidate.get("name"),
        "source_id": candidate.get("source_id"),
        "match_percent": match,
        "matched_required": matched_req,
        "missing_required": missing_req,
        "matched_nice_to_have": matched_nice,
        "experience_years": cand_years,
        "recommendation": recommendation,
    }


# --------------------------------------------------------------------------- #
# SKILL 4: email_drafter_skill
# --------------------------------------------------------------------------- #
def email_drafter_skill(
    candidate: Dict[str, Any],
    role_title: str,
    company: str = "NimbusPay Technologies",
    recruiter_name: str = "Fiza Asif",
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


def suggest_interview_slots(n: int, start_in_days: int = 3) -> List[str]:
    """Suggest ``n`` business-hour interview slots starting a few days out."""
    slots: List[str] = []
    day = datetime.now() + timedelta(days=start_in_days)
    hours = [10, 14, 16]  # 10:00, 14:00, 16:00
    i = 0
    while len(slots) < n:
        d = day + timedelta(days=i // len(hours))
        if d.weekday() < 5:  # Mon-Fri only
            hour = hours[i % len(hours)]
            slots.append(d.replace(hour=hour, minute=0, second=0, microsecond=0)
                         .strftime("%A, %d %b %Y at %H:%M IST"))
        i += 1
    return slots


__all__ = [
    "SKILL_VOCAB",
    "resume_parser_skill",
    "jd_parser_skill",
    "candidate_scorer_skill",
    "email_drafter_skill",
    "suggest_interview_slots",
]
