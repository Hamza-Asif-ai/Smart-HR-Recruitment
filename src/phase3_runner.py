"""Shared Phase 3 execution helpers.

This module is the single integration point used by both the CLI orchestrator
and the Gradio app:

* :func:`analyze_resume_text` runs the quarantined extractor (when enabled),
  verifies quotes, applies deterministic scoring, consults advisory memory, and
  writes bounded trace spans.
* :func:`run_coordinator` executes the ADK coordinator graph over validated
  profiles and inbox/local resume text. It never exposes raw resume text to the
  coordinator model: specialists only call deterministic tools.

No function here sends email. Drafts are staged through the approval queue.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from . import config, extractor, fairness, runtime, scoring, skills
from .approval import ApprovalQueue
from .models import CandidateProfile
from .security import Role


class CoordinatorUnavailable(RuntimeError):
    """The ADK coordinator cannot run (missing key, model, or google-adk)."""


class CoordinatorRunError(RuntimeError):
    """The coordinator finished without usable deterministic results."""


@dataclass
class ModelStatus:
    mode: str
    message: str
    model: str = ""

    @property
    def agentic(self) -> bool:
        return self.mode == "agentic"

    @property
    def degraded(self) -> bool:
        return self.mode == "degraded"


_MODEL_STATUS: Optional[ModelStatus] = None


def model_status(*, force: bool = False) -> ModelStatus:
    """Check GEMINI_MODEL once at startup without hardcoding a model name."""
    global _MODEL_STATUS
    if _MODEL_STATUS is not None and not force:
        return _MODEL_STATUS

    model = config.get_gemini_model()
    if not config.get_gemini_api_key():
        _MODEL_STATUS = ModelStatus(
            "deterministic",
            "No Gemini key configured; deterministic regex extraction is active.",
        )
        return _MODEL_STATUS
    if not model:
        _MODEL_STATUS = ModelStatus(
            "deterministic",
            "GEMINI_MODEL is not configured; deterministic regex extraction is active.",
        )
        return _MODEL_STATUS
    try:
        extractor.check_model_available(model)
    except Exception as exc:  # noqa: BLE001 - surfaced as a UI banner
        _MODEL_STATUS = ModelStatus(
            "degraded",
            f"Configured model is unavailable ({type(exc).__name__}); "
            "deterministic fallback is active.",
            model,
        )
        return _MODEL_STATUS
    _MODEL_STATUS = ModelStatus(
        "agentic", f"Phase 3 model '{model}' is available.", model
    )
    return _MODEL_STATUS


def _degrade(budget: Any, reason: str) -> None:
    try:
        budget.degraded = True
        budget.degraded_reason = reason
    except Exception:
        pass


def _memory_hint(memory: Any, email: str) -> Optional[str]:
    if memory is None or not email:
        return None
    try:
        return memory.hint_for(email)
    except Exception:
        return None


def _evidence_summary(profile: Any) -> Dict[str, Any]:
    """Return verified evidence quotes for the UI score cards."""
    try:
        experience = getattr(profile, "experience", None)
        return {
            "skills": [
                {"skill": claim.skill, "quote": claim.evidence_quote}
                for claim in getattr(profile, "skills", []) or []
                if claim.evidence_quote
            ],
            "experience": {
                "years": getattr(experience, "years", 0) or 0,
                "quote": getattr(experience, "evidence_quote", "") or "",
            },
            "education": [
                {"degree": claim.degree, "quote": claim.evidence_quote}
                for claim in getattr(profile, "education", []) or []
                if claim.evidence_quote
            ],
        }
    except Exception:  # noqa: BLE001 - evidence is optional UI data
        return {"skills": [], "experience": {"years": 0, "quote": ""}, "education": []}


def analyze_resume_text(
    resume_text: str,
    requirements: Dict[str, Any],
    *,
    source_id: str = "",
    run_context: Any = None,
    memory: Any = None,
    model: str = "",
    llm_enabled: bool = True,
    hidden_report: Any = None,
    budget_step: bool = True,
    include_profile: bool = False,
    blind: bool = False,
    known_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Analyze one resume through the Phase 3 quarantine/scoring blocks."""
    rc = run_context or runtime.new_run_context()
    if budget_step:
        rc.budget.consume()

    raw = str(resume_text or "")
    if blind:
        raw = fairness.blind_resume_text(raw, known_names)
    hidden = hidden_report
    mode = "deterministic"
    fallback_reason = ""

    with rc.trace.span("screener", "analyze_resume", tool="phase3",
                       input_summary=source_id or "resume"):
        profile = extractor.regex_candidate_profile(raw, source_id=source_id)
        if llm_enabled and not rc.budget.degraded:
            try:
                rc.budget.ensure_llm_available()
                selected_model = extractor.resolve_model(model)
                profile = extractor.extract_candidate_profile(
                    raw,
                    model=selected_model,
                    budget=rc.budget,
                    source_id=source_id,
                )
                mode = "validated_llm"
            except Exception as exc:  # noqa: BLE001 - deterministic safety net
                fallback_reason = rc.budget.degraded_reason or type(exc).__name__
                _degrade(rc.budget, fallback_reason)
                profile = extractor.regex_candidate_profile(raw, source_id=source_id)
                mode = "degraded"

        if blind:
            profile = fairness.blind_profile(profile)

        baseline = skills.resume_parser_skill(raw, source_id=source_id)
        hint = _memory_hint(memory, profile.email or baseline.get("email", ""))
        hidden_suspected = bool(
            profile.hidden_text_suspected
            or (hidden is not None and getattr(hidden, "detected", False))
        )
        result = scoring.score_validated_profile(
            profile,
            requirements,
            regex_skills=baseline.get("skills", []),
            regex_experienced_skills=baseline.get("experienced_skills", []),
            hidden_text_suspected=hidden_suspected,
            memory_hint=hint,
        )
        result["extraction_mode"] = mode
        result["_confidence"] = result.get("confidence", 0.0)
        result["skills"] = profile.skill_names()
        result["name"] = profile.name or baseline.get("name", "Unknown Candidate")
        result["email"] = profile.email or baseline.get("email", "")
        result["source_id"] = source_id or profile.source_id
        result["evidence"] = _evidence_summary(profile)
        result["blind_mode"] = bool(blind)
        if hidden is not None:
            try:
                result.update(hidden.to_dict())
            except Exception:
                pass
        rc.trace.record(
            "screener",
            "phase3_result",
            tool=mode,
            output_summary=(
                f"{result.get('match_percent', 0)}% "
                f"confidence={result.get('confidence', 0)} "
                f"review={result.get('needs_human_review', False)}"
            ),
        )

    if mode == "degraded":
        rc.trace.record(
            "screener", "degradation", tool="phase3",
            output_summary=fallback_reason,
        )
    if include_profile:
        result["_profile"] = profile
    return result


def _session_state(
    *,
    role: str,
    requirements: Dict[str, Any],
    resume_texts: Mapping[str, str],
    validated_profiles: Mapping[str, CandidateProfile],
    run_context: Any,
    memory: Any,
    approval_queue: ApprovalQueue,
    hidden_reports: Mapping[str, Any],
    jd_filename: str,
) -> Dict[str, Any]:
    return {
        "role": role,
        "user": "adk_coordinator",
        "jd_filename": jd_filename,
        "jd_requirements": requirements,
        "resume_texts": dict(resume_texts),
        "validated_profiles": dict(validated_profiles),
        "hidden_reports": dict(hidden_reports),
        "budget": run_context.budget,
        "trace": run_context.trace,
        "memory": memory,
        "approval_queue": approval_queue,
    }


async def run_coordinator_async(
    *,
    requirements: Dict[str, Any],
    resume_texts: Mapping[str, str],
    source_ids: Optional[Sequence[str]] = None,
    validated_profiles: Optional[Mapping[str, CandidateProfile]] = None,
    hidden_reports: Optional[Mapping[str, Any]] = None,
    role: str = "recruiter",
    model: str = "",
    run_context: Any = None,
    memory: Any = None,
    approval_queue: Optional[ApprovalQueue] = None,
    jd_filename: str = "",
) -> Dict[str, Any]:
    """Execute the ADK coordinator graph over already-quarantined data."""
    from . import coordinator

    if not coordinator.ADK_AVAILABLE:
        raise CoordinatorUnavailable("google-adk is not installed.")
    if not config.get_gemini_api_key():
        raise CoordinatorUnavailable("Set GOOGLE_API_KEY or GEMINI_API_KEY.")
    selected_model = extractor.resolve_model(model)
    Role(role)  # validate before creating a session

    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    rc = run_context or runtime.new_run_context()
    queue = approval_queue or ApprovalQueue()
    ids = [str(s) for s in (source_ids or list(resume_texts.keys()))]
    app_name = "smart_hr_recruitment"
    user_id = "adk_coordinator"
    session_id = f"phase3-{uuid.uuid4().hex}"
    service = InMemorySessionService()
    state = _session_state(
        role=role,
        requirements=requirements,
        resume_texts=resume_texts,
        validated_profiles=validated_profiles or {},
        run_context=rc,
        memory=memory,
        approval_queue=queue,
        hidden_reports=hidden_reports or {},
        jd_filename=jd_filename,
    )
    await service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state=state,
    )

    root = coordinator.build_coordinator(selected_model, source_ids=ids)
    runner = Runner(app_name=app_name, agent=root, session_service=service)
    message = types.Content(
        role="user",
        parts=[types.Part(text=(
            "Run the Phase 3 recruitment review now. Call "
            "analyze_job_description once, then call screen_resume once for "
            "each assigned source id, then run the critic. Do not re-read raw "
            "resume text and never send email; drafts go to the approval queue."
        ))],
    )

    try:
        async for _event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            pass
        session = await service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
    except Exception as exc:  # noqa: BLE001 - normalised for the caller
        raise CoordinatorRunError(
            f"ADK coordinator run failed ({type(exc).__name__})."
        ) from exc

    final_state = dict(getattr(session, "state", {}) or {})
    ranked = final_state.get("ranked_candidates")
    if not isinstance(ranked, list) or not ranked:
        raise CoordinatorRunError("Coordinator did not produce ranked candidates.")
    if ids and len(ranked) < len(ids):
        raise CoordinatorRunError(
            f"Coordinator screened {len(ranked)} of {len(ids)} resumes."
        )
    return {
        "mode": "coordinator",
        "ranked_candidates": ranked,
        "pending_drafts": [
            item.as_dict() for item in queue.list()
        ],
        "critique_issues": final_state.get("critique_issues", []),
        "budget": rc.budget.snapshot(),
        "trace": rc.trace.get_traces(limit=1000),
        "run_id": rc.trace.run_id,
    }


def run_coordinator(**kwargs: Any) -> Dict[str, Any]:
    """Synchronous wrapper around :func:`run_coordinator_async`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_coordinator_async(**kwargs))
    raise CoordinatorUnavailable(
        "run_coordinator() cannot be called from a running event loop; "
        "use 'await run_coordinator_async(...)' instead."
    )


def coordinator_available() -> bool:
    try:
        from . import coordinator

        return bool(
            coordinator.ADK_AVAILABLE
            and config.get_gemini_api_key()
            and config.get_gemini_model()
        )
    except Exception:
        return False


def summarize_budget(run_context: Any) -> str:
    """Human-readable budget/degradation line for the UI."""
    if run_context is None:
        return ""
    try:
        snap = run_context.budget.snapshot()
    except Exception:
        return ""
    line = (
        f"Run {run_context.trace.run_id[:8]} · "
        f"LLM calls {snap['model_calls']}/{snap['max_llm_calls']} · "
        f"tokens {snap['tokens']}/{snap['max_tokens']} · "
        f"{snap['elapsed_seconds']:.1f}s · "
        f"steps {snap['steps_used']}/{snap['max_steps']}"
    )
    banner = ""
    try:
        banner = run_context.budget.banner()
    except Exception:
        banner = ""
    return line + (f" — {banner}" if banner else "")


__all__ = [
    "CoordinatorRunError",
    "CoordinatorUnavailable",
    "ModelStatus",
    "analyze_resume_text",
    "coordinator_available",
    "model_status",
    "run_coordinator",
    "run_coordinator_async",
    "summarize_budget",
]
