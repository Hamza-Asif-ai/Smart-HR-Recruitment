"""Minimal, role-bound ADK tools.

Only these callables are exposed to models. There is deliberately no
``send_invitation`` tool: drafting writes to an approval queue, and the
deterministic mailer in :mod:`src.approval` is the only send path.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import extractor, mcp_filesystem_server as fs, skills
from .approval import ApprovalQueue
from .models import ReplyTriage
from .security import Role, SecurityContext, is_valid_email, mask_pii

try:  # pragma: no cover - ADK is optional for offline tests
    from google.adk.tools import ToolContext
except Exception:  # pragma: no cover
    ToolContext = Any  # type: ignore

TOOL_CAPABILITIES: Dict[str, str] = {
    "analyze_job_description": "read_resume",
    "screen_resume": "score_candidates",
    "extract_resume_profile": "read_resume",
    "draft_invitation_email": "send_invitations",
    "evaluate_critique": "score_candidates",
    "triage_reply": "read_resume",
}

ROLE_TOOLS: Dict[str, List[str]] = {
    Role.ADMIN.value: list(TOOL_CAPABILITIES),
    Role.RECRUITER.value: list(TOOL_CAPABILITIES),
    Role.HIRING_MANAGER.value: [
        "analyze_job_description", "screen_resume", "extract_resume_profile",
        "evaluate_critique", "triage_reply",
    ],
    Role.VIEWER.value: [],
}

_BAD_ARGUMENT_RE = re.compile(r"[\r\n\x00]")


def _state(tool_context: Any) -> Dict[str, Any]:
    state = getattr(tool_context, "state", None)
    if state is None:
        raise RuntimeError("ADK tool context is missing shared session state")
    return state


def _security_context(tool_context: Any) -> SecurityContext:
    state = _state(tool_context)
    role_value = str(state.get("role", "viewer"))
    try:
        role = Role(role_value)
    except ValueError as exc:
        raise RuntimeError(f"Unknown role in session state: {role_value!r}") from exc
    return SecurityContext(user=str(state.get("user", "adk_agent")), role=role)


def _queue(tool_context: Any) -> ApprovalQueue:
    state = _state(tool_context)
    queue = state.get("approval_queue")
    if not isinstance(queue, ApprovalQueue):
        queue = ApprovalQueue()
        state["approval_queue"] = queue
    return queue


def _trace(tool_context: Any, tool: str, summary: str) -> None:
    trace = _state(tool_context).get("trace")
    if trace is not None and hasattr(trace, "record"):
        trace.record("tool", "tool_call", tool=tool, output_summary=mask_pii(summary))


def _clean_argument(value: Any, name: str, limit: int = 500) -> str:
    text = str(value or "")
    if _BAD_ARGUMENT_RE.search(text):
        raise ValueError(f"{name} contains CR/LF/NUL")
    return text[:limit]


def analyze_job_description(tool_context: ToolContext, jd_filename: str = "") -> Dict[str, Any]:
    """Parse the configured job description into structured requirements."""
    ctx = _security_context(tool_context)
    ctx.authorize("read_resume")
    state = _state(tool_context)
    supplied = state.get("jd_requirements")
    if isinstance(supplied, dict) and supplied.get("required_skills") is not None:
        requirements = supplied
    else:
        jd_filename = _clean_argument(jd_filename, "jd_filename", 255)
        requirements = skills.jd_parser_skill(fs.read_job_description(jd_filename))
    state["jd_requirements"] = requirements
    _trace(tool_context, "analyze_job_description", "jd parsed")
    return {"status": "ok", "title": requirements["title"],
            "required_skills": len(requirements["required_skills"])}


def extract_resume_profile(tool_context: ToolContext, source_id: str,
                           resume_text: str = "") -> Dict[str, Any]:
    """Return a validated profile. Raw untrusted text is never echoed back."""
    ctx = _security_context(tool_context)
    ctx.authorize("read_resume")
    source_id = _clean_argument(source_id, "source_id", 255)
    state = _state(tool_context)
    supplied_texts = state.get("resume_texts")
    text = resume_text
    if not text and isinstance(supplied_texts, dict):
        text = str(supplied_texts.get(source_id, "") or "")
    if not text:
        text = fs.read_resume(source_id)
    profile = extractor.regex_candidate_profile(text, source_id=source_id)
    state["latest_profile"] = profile
    state.setdefault("validated_profiles", {})[source_id] = profile
    _trace(tool_context, "extract_resume_profile", "profile extracted")
    return {
        "status": "ok",
        "source_id": profile.source_id,
        "verified_skills": len(profile.skills),
        "dropped_claims": profile.dropped_claims,
        "injection_suspected": profile.injection_suspected,
    }


def screen_resume(tool_context: ToolContext, source_id: str) -> Dict[str, Any]:
    """Score one resume deterministically from a validated profile."""
    ctx = _security_context(tool_context)
    ctx.authorize("read_resume")
    ctx.authorize("score_candidates")
    state = _state(tool_context)
    requirements = state.get("jd_requirements")
    if not isinstance(requirements, dict):
        raise RuntimeError("jd_requirements is missing; run the JD analyst first")
    source_id = _clean_argument(source_id, "source_id", 255)

    supplied_texts = state.get("resume_texts")
    text = str(supplied_texts.get(source_id, "") or "") if isinstance(
        supplied_texts, dict) else ""
    validated = state.get("validated_profiles")
    profile = validated.get(source_id) if isinstance(validated, dict) else None
    if profile is None:
        if not text:
            text = fs.read_resume(source_id)
        profile = extractor.regex_candidate_profile(text, source_id=source_id)
    elif not text:
        # No raw text available: use a verified deterministic baseline only.
        pass

    baseline = skills.resume_parser_skill(text, source_id) if text else {}
    regex_skills = baseline.get("skills", []) if text else profile.skill_names()
    regex_experienced = baseline.get("experienced_skills", []) if text else profile.skill_names()
    memory = state.get("memory")
    hint = None
    if memory is not None and getattr(profile, "email", ""):
        try:
            hint = memory.hint_for(profile.email)
        except Exception:
            hint = None
    hidden_reports = state.get("hidden_reports")
    hidden = hidden_reports.get(source_id) if isinstance(hidden_reports, dict) else None
    from .scoring import score_validated_profile

    scored = score_validated_profile(
        profile, requirements,
        regex_skills=regex_skills,
        regex_experienced_skills=regex_experienced,
        hidden_text_suspected=bool(getattr(hidden, "detected", False)),
        memory_hint=hint,
    )
    scored["source_id"] = source_id
    scored["_confidence"] = scored.get("confidence", 0.0)
    state.setdefault("ranked_candidates", []).append(scored)
    _trace(tool_context, "screen_resume", f"scored {source_id}")
    return {"status": "ok", "source_id": source_id,
            "match_percent": scored["match_percent"],
            "confidence": scored["confidence"],
            "needs_human_review": scored["needs_human_review"]}


def draft_invitation_email(tool_context: ToolContext, candidate_email: str,
                           subject: str, body: str, candidate_name: str = "",
                           slot: str = "", company: str = "") -> Dict[str, Any]:
    """Stage a draft in the approval queue. This tool never sends mail."""
    ctx = _security_context(tool_context)
    ctx.authorize("send_invitations")
    email = _clean_argument(candidate_email, "candidate_email", 254).strip()
    if not is_valid_email(email):
        raise ValueError("candidate_email is invalid")
    subject = _clean_argument(subject, "subject", 200)
    body = _clean_argument(body, "body", 20_000)
    if not subject or not body:
        raise ValueError("subject and body are required")
    item = _queue(tool_context).stage({
        "to": email,
        "subject": subject,
        "body": body,
        "candidate": _clean_argument(candidate_name, "candidate_name", 120),
        "html": "",
    })
    _state(tool_context).setdefault("approval_pending", []).append(item.as_dict())
    _trace(tool_context, "draft_invitation_email", "draft staged for approval")
    return {"status": "staged_for_approval", "draft_id": item.id,
            "approval_required": True}


def evaluate_critique(tool_context: ToolContext, issues: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Record critic issues; exit the LoopAgent when there are none."""
    _security_context(tool_context).authorize("score_candidates")
    clean = []
    for issue in issues or []:
        message = _clean_argument(issue.get("message", ""), "message", 500)
        severity = str(issue.get("severity", "medium")).lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        if message:
            clean.append({"severity": severity, "message": message})
    state = _state(tool_context)
    state["critique_issues"] = clean
    if not clean:
        try:
            tool_context.actions.escalate = True
        except Exception:
            pass
    _trace(tool_context, "evaluate_critique", f"{len(clean)} issue(s)")
    return {"issues": len(clean), "exit": not clean}


def triage_reply(tool_context: ToolContext, body: str) -> Dict[str, Any]:
    """Classify a reply with a deterministic confidence; low confidence -> human."""
    _security_context(tool_context).authorize("read_resume")
    from .guardrails import classify_reply

    result: ReplyTriage = classify_reply(_clean_argument(body, "body", 20_000))
    _state(tool_context)["reply_triage"] = result.model_dump()
    _trace(tool_context, "triage_reply", f"intent={result.intent}")
    return result.model_dump()


__all__ = [
    "ROLE_TOOLS",
    "TOOL_CAPABILITIES",
    "analyze_job_description",
    "draft_invitation_email",
    "evaluate_critique",
    "extract_resume_profile",
    "screen_resume",
    "triage_reply",
]
