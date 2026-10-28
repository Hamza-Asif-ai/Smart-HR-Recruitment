"""ADK stage tools.

Each tool delegates to one deterministic pipeline stage and stores the full
result in ADK session state. The LLM only chooses when to call the tool; the
ranking, filtering, and email drafting remain deterministic and auditable.

The summaries returned to the LLM deliberately contain counts instead of
candidate names, emails, or resume text, so untrusted resume content never
enters the model context.
"""

from __future__ import annotations

from typing import Any, Dict

try:  # pragma: no cover - exercised only when google-adk is installed
    from google.adk.tools import ToolContext
except Exception:  # pragma: no cover
    ToolContext = Any  # type: ignore

from . import orchestrator
from .security import SecurityContext, Role


def _state(tool_context: Any) -> Dict[str, Any]:
    state = getattr(tool_context, "state", None)
    if state is None:
        raise RuntimeError("ADK tool context is missing shared session state")
    return state


def _context(tool_context: Any) -> SecurityContext:
    role_value = str(_state(tool_context).get("role", "viewer"))
    try:
        role = Role(role_value)
    except ValueError as exc:
        raise RuntimeError(f"Unknown role in session state: {role_value!r}") from exc
    return SecurityContext(user="adk_pipeline", role=role)


def _record_audit(tool_context: Any, ctx: SecurityContext) -> None:
    state = _state(tool_context)
    audit = list(state.get("audit_log", []))
    audit.extend(ctx.audit_log)
    state["audit_log"] = audit


def adk_jd_stage(tool_context: ToolContext, jd_filename: str = "") -> Dict[str, Any]:
    """Parse the job description deterministically and store the requirements."""
    state = _state(tool_context)
    ctx = _context(tool_context)
    requirements = orchestrator.run_jd_agent(
        ctx, jd_filename or state.get("jd_filename", "")
    )
    state["jd_requirements"] = requirements
    _record_audit(tool_context, ctx)
    return {
        "status": "ok",
        "title": requirements["title"],
        "required_skills": len(requirements["required_skills"]),
        "nice_to_have": len(requirements["nice_to_have"]),
        "min_years": requirements["min_years"],
    }


def adk_screening_stage(tool_context: ToolContext) -> Dict[str, Any]:
    """Screen every resume, storing ranked candidates in session state."""
    state = _state(tool_context)
    requirements = state.get("jd_requirements")
    if not isinstance(requirements, dict):
        raise RuntimeError("jd_requirements is missing; run the JD stage first")

    ctx = _context(tool_context)
    ranked = orchestrator.run_resume_screener(ctx, requirements)
    state["ranked_candidates"] = ranked
    _record_audit(tool_context, ctx)
    top_score = ranked[0]["match_percent"] if ranked else 0.0
    return {
        "status": "ok",
        "screened": len(ranked),
        "unreadable": sum(1 for c in ranked if c.get("parse_status") == "unreadable"),
        "keyword_stuffing_suspected": sum(
            1 for c in ranked if c.get("keyword_stuffing_suspected")
        ),
        "top_score": top_score,
    }


def adk_scheduler_stage(tool_context: ToolContext) -> Dict[str, Any]:
    """Draft invitations for shortlistable candidates and store the drafts."""
    state = _state(tool_context)
    requirements = state.get("jd_requirements")
    ranked = state.get("ranked_candidates")
    if not isinstance(requirements, dict) or not isinstance(ranked, list):
        raise RuntimeError("Screening results are missing; run the screening stage first")

    ctx = _context(tool_context)
    if ctx.can("send_invitations"):
        emails = orchestrator.run_scheduler(ctx, ranked, requirements["title"])
    else:
        emails = []
        ctx.audit_log.append({
            "user": ctx.user,
            "role": ctx.role.value,
            "capability": "send_invitations",
            "granted": False,
        })
    state["interview_emails"] = emails
    _record_audit(tool_context, ctx)
    return {
        "status": "ok",
        "invitations": len(emails),
        "scheduling_skipped": not ctx.can("send_invitations"),
    }


def adk_insights_stage(tool_context: ToolContext) -> Dict[str, Any]:
    """Build and save the insights report, storing metrics in session state."""
    state = _state(tool_context)
    requirements = state.get("jd_requirements")
    ranked = state.get("ranked_candidates")
    emails = state.get("interview_emails")
    if not isinstance(requirements, dict) or not isinstance(ranked, list):
        raise RuntimeError("Pipeline results are missing; run the earlier stages first")
    if not isinstance(emails, list):
        raise RuntimeError("interview_emails is missing; run the scheduler stage first")

    ctx = _context(tool_context)
    insights = orchestrator.run_insights(ctx, requirements, ranked, emails)
    state["insights"] = insights
    _record_audit(tool_context, ctx)
    return {
        "status": "ok",
        "total_applicants": insights["total_applicants"],
        "shortlisted": insights["shortlisted"],
        "average_match_percent": insights["average_match_percent"],
    }


__all__ = [
    "adk_jd_stage",
    "adk_screening_stage",
    "adk_scheduler_stage",
    "adk_insights_stage",
]
