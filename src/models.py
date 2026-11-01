"""Pydantic schemas shared by the quarantined extractor and ADK specialists.

The extractor is the only model that sees untrusted resume text. It has no
tools and must return one of these schemas; deterministic code then verifies
every evidence quote against the original text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal

try:
    from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
except Exception as exc:  # pragma: no cover - dependency is pinned
    raise RuntimeError("Phase 3 requires pydantic>=2") from exc


class SkillClaim(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    skill: str = Field(validation_alias=AliasChoices("skill", "name", "canonical"))
    evidence_quote: str = Field(default="", validation_alias=AliasChoices(
        "evidence_quote", "evidence", "quote"))

    @field_validator("skill")
    @classmethod
    def _clean_skill(cls, value: str) -> str:
        return " ".join(str(value).lower().split())[:80]


class ExperienceClaim(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    years: float = Field(default=0.0, validation_alias=AliasChoices(
        "years", "experience_years", "value"))
    evidence_quote: str = Field(default="", validation_alias=AliasChoices(
        "evidence_quote", "evidence", "quote"))

    @field_validator("years")
    @classmethod
    def _clamp_years(cls, value: Any) -> float:
        try:
            years = float(value or 0)
        except (TypeError, ValueError):
            return 0.0
        return round(max(0.0, min(years, 60.0)), 1)


class EducationClaim(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    degree: str = Field(default="", validation_alias=AliasChoices(
        "degree", "qualification"))
    institution: str = ""
    evidence_quote: str = Field(default="", validation_alias=AliasChoices(
        "evidence_quote", "evidence", "quote"))


class CandidateProfile(BaseModel):
    """Validated profile returned by the no-tool extractor."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = ""
    email: str = ""
    phone: str = ""
    skills: List[SkillClaim] = Field(default_factory=list)
    experience: ExperienceClaim = Field(default_factory=ExperienceClaim)
    education: List[EducationClaim] = Field(default_factory=list)
    injection_suspected: bool = False
    hidden_text_suspected: bool = False
    source_id: str = ""
    parse_status: str = "ok"
    warnings: List[str] = Field(default_factory=list)
    dropped_claims: int = 0
    verification_rate: float = 0.0

    @property
    def experience_years(self) -> float:
        return self.experience.years

    def skill_names(self) -> List[str]:
        return [s.skill for s in self.skills if s.skill]


class ReplyTriage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Literal["confirm", "reschedule", "decline", "question"] = "question"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_human: bool = True
    reason: str = ""


class EmailDraft(BaseModel):
    """Draft-only schema for the email writer. It can never send mail."""

    model_config = ConfigDict(extra="ignore")

    to: str = ""
    candidate: str = ""
    subject: str = ""
    body: str = ""
    slot: str = ""
    approval_required: bool = True


class CritiqueIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: Literal["low", "medium", "high"] = "medium"
    message: str = ""


class CritiqueReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    issues: List[CritiqueIssue] = Field(default_factory=list)
    approved: bool = True


def profile_to_candidate(profile: CandidateProfile) -> Dict[str, Any]:
    """Convert a validated profile into the deterministic scorer's input dict."""
    return {
        "name": profile.name,
        "email": profile.email,
        "phone": profile.phone,
        "source_id": profile.source_id,
        "skills": profile.skill_names(),
        "experienced_skills": profile.skill_names(),
        "experience_years": profile.experience_years,
        "education": [e.degree for e in profile.education if e.degree],
        "parse_status": profile.parse_status,
        "parse_warnings": list(profile.warnings),
        "injection_suspected": profile.injection_suspected,
        "hidden_text_suspected": profile.hidden_text_suspected,
        "verification_rate": profile.verification_rate,
        "dropped_claims": profile.dropped_claims,
    }


__all__ = [
    "CandidateProfile",
    "CritiqueIssue",
    "CritiqueReport",
    "EducationClaim",
    "EmailDraft",
    "ExperienceClaim",
    "ReplyTriage",
    "SkillClaim",
    "profile_to_candidate",
]
