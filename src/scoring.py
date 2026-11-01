"""Deterministic scoring over a verified extractor profile.

The scorer never trusts model prose directly: it consumes the validated
``CandidateProfile``, scores with the existing evidence-weighted formula, and
compares the model's verified skills with the deterministic regex extractor.
Large disagreement becomes a review flag and lowers a real confidence value.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from . import config, skills
from .models import CandidateProfile, profile_to_candidate


def skill_disagreement(llm_skills: Iterable[str], regex_skills: Iterable[str]) -> float:
    """Return Jaccard distance (0 = identical, 1 = no overlap)."""
    llm = {str(s).strip().lower() for s in llm_skills if str(s).strip()}
    regex = {str(s).strip().lower() for s in regex_skills if str(s).strip()}
    union = llm | regex
    if not union:
        return 0.0
    return round(len(llm ^ regex) / len(union), 3)


def _coerce_profile(profile: Any) -> CandidateProfile:
    if isinstance(profile, CandidateProfile):
        return profile
    if isinstance(profile, dict):
        return CandidateProfile.model_validate(profile)
    raise TypeError("profile must be a CandidateProfile or dict")


def compute_confidence(
    *,
    verification_rate: float,
    disagreement: float,
    injection_suspected: bool,
    hidden_text_suspected: bool,
    parse_status: str,
) -> float:
    """A bounded, explainable confidence in the extracted profile."""
    if parse_status == "unreadable":
        return 0.0
    verification = max(0.0, min(1.0, float(verification_rate)))
    agreement = max(0.0, min(1.0, 1.0 - float(disagreement)))
    integrity = 1.0
    if injection_suspected:
        integrity -= 0.35
    if hidden_text_suspected:
        integrity -= 0.35
    score = 0.45 * verification + 0.40 * agreement + 0.15 * max(0.0, integrity)
    return round(max(0.0, min(1.0, score)), 3)


def score_validated_profile(
    profile: Any,
    requirements: Dict[str, Any],
    *,
    regex_skills: Optional[Iterable[str]] = None,
    regex_experienced_skills: Optional[Iterable[str]] = None,
    hidden_text_suspected: Optional[bool] = None,
    memory_hint: Optional[str] = None,
    disagreement_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Score a verified profile and flag extraction disagreement for humans."""
    validated = _coerce_profile(profile)
    candidate = profile_to_candidate(validated)
    if regex_experienced_skills is not None:
        candidate["experienced_skills"] = [
            str(skill).strip().lower() for skill in regex_experienced_skills
            if str(skill).strip()
        ]
    result = skills.candidate_scorer_skill(candidate, requirements)

    regex_set = {str(s).lower() for s in (regex_skills or [])}
    disagreement = skill_disagreement(validated.skill_names(), regex_set)
    threshold = (config.SKILL_DISAGREEMENT_THRESHOLD
                 if disagreement_threshold is None else float(disagreement_threshold))
    hidden = (validated.hidden_text_suspected if hidden_text_suspected is None
              else bool(hidden_text_suspected))

    confidence = compute_confidence(
        verification_rate=validated.verification_rate,
        disagreement=disagreement,
        injection_suspected=validated.injection_suspected,
        hidden_text_suspected=hidden,
        parse_status=validated.parse_status,
    )

    reasons: List[str] = []
    needs_review = False
    if disagreement > threshold:
        needs_review = True
        reasons.append(
            f"LLM and regex skills disagree ({disagreement:.0%} > {threshold:.0%})")
    if validated.injection_suspected:
        needs_review = True
        reasons.append("prompt-injection indicators detected")
    if hidden:
        needs_review = True
        reasons.append("hidden PDF text detected")
    if validated.dropped_claims:
        reasons.append(f"{validated.dropped_claims} unverified claim(s) dropped")
    if result.get("keyword_stuffing_suspected"):
        needs_review = True
        reasons.append("keyword stuffing suspected")

    if needs_review and result.get("shortlistable", True):
        result["recommendation"] = "Possible match - review (needs human verification)"

    result.update({
        "confidence": confidence,
        "confidence_reason": "; ".join(reasons) if reasons else "extraction verified",
        "needs_human_review": needs_review,
        "extraction_disagreement": disagreement,
        "extraction_disagreement_threshold": threshold,
        "verification_rate": validated.verification_rate,
        "dropped_claims": validated.dropped_claims,
        "injection_suspected": validated.injection_suspected,
        "hidden_text_suspected": hidden,
        "memory_hint": memory_hint,
        "extraction_mode": "validated_llm",
        "source_id": validated.source_id or result.get("source_id"),
    })
    return result


score_candidate = score_validated_profile


__all__ = [
    "compute_confidence",
    "score_candidate",
    "score_validated_profile",
    "skill_disagreement",
]
