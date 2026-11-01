"""ADK callbacks for budget, unicode/PII hygiene, RBAC, and trace spans."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional

from . import config
from .budget import RunBudget, usage_token_count
from .models import ReplyTriage
from .security import Role, SecurityContext, is_valid_email, mask_pii
from .tools import ROLE_TOOLS, TOOL_CAPABILITIES

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_BAD_ARGUMENT_RE = re.compile(r"[\r\n\x00]")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_ZERO_WIDTH)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")


def minimize_pii(value: str) -> str:
    return _EMAIL_RE.sub("[EMAIL]", normalize_text(value))


def _state(callback_context: Any) -> Any:
    state = getattr(callback_context, "state", None)
    return state if state is not None else {}


def _get(state: Any, key: str, default: Any = None) -> Any:
    try:
        return state.get(key, default)
    except Exception:
        return default


def _set(state: Any, key: str, value: Any) -> None:
    try:
        state[key] = value
    except Exception:
        pass


def _budget(callback_context: Any) -> RunBudget:
    state = _state(callback_context)
    budget = _get(state, "budget")
    if not isinstance(budget, RunBudget):
        budget = RunBudget()
        _set(state, "budget", budget)
    return budget


def _role(callback_context: Any) -> Role:
    try:
        return Role(str(_get(_state(callback_context), "role", "viewer")))
    except ValueError:
        return Role.VIEWER


def _trace(callback_context: Any) -> Any:
    return _get(_state(callback_context), "trace")


def _record(callback_context: Any, tool: str, action: str,
            summary: str = "", duration_ms: float = 0.0) -> None:
    trace = _trace(callback_context)
    if trace is not None and hasattr(trace, "record"):
        trace.record(
            getattr(callback_context, "agent_name", "agent") or "agent",
            action,
            tool=tool,
            output_summary=mask_pii(summary),
            duration_ms=duration_ms,
        )


def before_model_callback(callback_context: Any, llm_request: Any) -> None:
    """Consume one model-call budget unit, then normalise/minimise content."""
    budget = _budget(callback_context)
    budget.before_model_call()
    _set(_state(callback_context), "budget", budget)
    for content in getattr(llm_request, "contents", None) or []:
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                part.text = minimize_pii(text)
    _record(callback_context, "model", "before_model")
    return None


def after_model_callback(callback_context: Any, llm_response: Any) -> None:
    """Account real usage_metadata tokens and trace the response."""
    budget = _budget(callback_context)
    usage = getattr(llm_response, "usage_metadata", None)
    budget.record_tokens(usage_token_count(usage))
    if budget.degraded:
        _set(_state(callback_context), "degradation_banner", budget.banner())
    _set(_state(callback_context), "budget", budget)
    summary = getattr(llm_response, "text", "") or ""
    _record(callback_context, "model", "after_model", str(summary)[:180])
    return None


def validate_tool_arguments(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    if not isinstance(args, dict):
        return "tool arguments must be an object"
    for key, value in args.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", str(key)):
            return "invalid argument name"
        if isinstance(value, str):
            if _BAD_ARGUMENT_RE.search(value):
                return f"{key} contains CR/LF/NUL"
            if len(value) > (20_000 if key == "body" else 500):
                return f"{key} is too long"
    email = args.get("candidate_email") or args.get("to")
    if email and not is_valid_email(email):
        return "invalid email address"
    return None


def before_tool_callback(tool: Any, args: Dict[str, Any], callback_context: Any) -> Optional[Dict[str, Any]]:
    """RBAC + argument validation; returning a dict blocks the real tool."""
    name = str(getattr(tool, "name", "") or getattr(tool, "__name__", ""))
    if name not in TOOL_CAPABILITIES:
        _record(callback_context, name or "unknown", "tool_denied", "unknown tool")
        return {"status": "denied", "reason": "tool is not in the allow-list"}
    role = _role(callback_context)
    if name not in ROLE_TOOLS.get(role.value, []):
        _record(callback_context, name, "tool_denied", "role not allowed")
        return {"status": "denied", "reason": "role is not allowed to use this tool"}
    problem = validate_tool_arguments(name, args or {})
    if problem:
        _record(callback_context, name, "tool_denied", problem)
        return {"status": "denied", "reason": problem}
    ctx = SecurityContext(user=str(_get(_state(callback_context), "user", "adk")), role=role)
    try:
        ctx.authorize(TOOL_CAPABILITIES[name])
    except Exception:
        _record(callback_context, name, "tool_denied", "capability denied")
        return {"status": "denied", "reason": "capability denied"}
    _set(_state(callback_context), f"_tool_started_{name}", True)
    _record(callback_context, name, "before_tool", "arguments validated")
    return None


def after_tool_callback(tool: Any, args: Dict[str, Any], callback_context: Any,
                        result: Any) -> None:
    """Trace tool completion, masking any PII or secret-like text."""
    name = str(getattr(tool, "name", "") or getattr(tool, "__name__", ""))
    _set(_state(callback_context), f"_tool_started_{name}", False)
    summary = str(result)[:180] if result is not None else ""
    _record(callback_context, name, "after_tool", summary)
    return None


_REPLY_MARKERS = {
    "decline": [
        "decline", "not interested", "no longer available", "not available",
        "unavailable", "cannot attend", "can't attend", "cannot join",
        "can't join", "will not attend", "won't attend", "withdraw", "pass on",
        "accepted another offer", "accepted a different offer", "another offer",
        "no thanks", "not proceeding",
        "nahi aa sakta", "nahi aa sakti", "nahi aa sakte", "nahi kar sakta",
        "nahi kar sakti", "nahi chahiye", "mujhe nahi", "join nahi",
        "available nahi", "dusri job", "doosri naukri", "accept kar li",
        "دستیاب نہیں", "نہیں آ سکتا", "نہیں آ سکتی", "نہیں کر سکتا",
        "دوسری نوکری", "قبول کر لی", "دلچسپی نہیں", "شامل نہیں",
    ],
    "reschedule": [
        "reschedule", "another time", "another day", "change the time",
        "change time", "different day", "different time", "postpone",
        "move the interview", "move it", "later slot", "next week",
        "time badal", "waqt change", "waqt badal", "doosra din", "agla hafta",
        "وقت تبدیل", "وقت بدل", "دوسرا دن", "اگلے ہفتے", "ملتوی",
    ],
    "confirm": [
        "confirm", "yes", "i can attend", "i will attend", "i'll attend",
        "i am available", "i'm available", "available for", "available on",
        "works for me", "works well", "see you", "accepted the interview",
        "haan", "ji haan", "aa sakta", "aa sakti", "aa sakte", "confirm karta",
        "confirm karti", "theek hai", "zaroor", "manzoor", "main aaunga",
        "ہاں", "جی ہاں", "تصدیق", "آ سکتا", "آ سکتی", "منظور", "ٹھیک ہے",
        "ضرور",
    ],
    "question": [
        "?", "؟", "question", "could you", "can you", "what is", "how long",
        "where is", "what time", "clarify", "details", "more information",
        "kya", "kaise", "kab", "kahan", "bata", "maloom", "تفصیل",
        "کیا", "کیسے", "کب", "کہاں", "بتا", "معلومات",
    ],
}

_STRONG_MARKERS = {
    "decline": [
        "not available", "no longer available", "unavailable", "cannot attend", "can't attend",
        "cannot join", "can't join", "not interested", "accepted another offer",
        "accepted a different offer", "nahi aa sakta", "nahi aa sakti",
        "nahi aa sakte", "nahi kar sakta", "nahi kar sakti", "nahi chahiye",
        "available nahi", "accept kar li", "دستیاب نہیں", "نہیں آ سکتا",
        "نہیں آ سکتی", "قبول کر لی", "شامل نہیں",
    ],
    "reschedule": [
        "change the time", "change time", "reschedule", "another time",
        "postpone", "different time", "different day",
        "waqt change", "waqt badal", "time badal", "وقت تبدیل", "وقت بدل",
    ],
    "confirm": [
        "i can attend", "i will attend", "i'll attend", "i am available",
        "i'm available", "confirm karta", "confirm karti", "ji haan",
        "تصدیق", "منظور",
    ],
}


def _marker_hits(text: str, markers: dict[str, list[str]]) -> dict[str, int]:
    return {
        intent: sum(1 for marker in words if marker in text)
        for intent, words in markers.items()
    }


def classify_reply(body: str) -> ReplyTriage:
    """Deterministic multilingual reply triage with an explainable confidence.

    Priority is deliberate: an explicit negative (\"not available\", \"accepted
    another offer\", Roman Urdu/Urdu equivalents) must never be misread as a
    confirmation merely because it also contains the word \"available\".
    """
    text = normalize_text(body or "").lower()
    if not text.strip():
        return ReplyTriage(intent="question", confidence=0.0,
                           requires_human=True, reason="empty reply")

    strong = _marker_hits(text, _STRONG_MARKERS)
    for intent in ("decline", "reschedule", "confirm"):
        if strong[intent]:
            confidence = round(min(0.95, 0.82 + 0.04 * strong[intent]), 3)
            return ReplyTriage(
                intent=intent,
                confidence=confidence,
                requires_human=confidence < config.REPLY_CONFIDENCE_THRESHOLD,
                reason="explicit deterministic signal",
            )

    scores = _marker_hits(text, _REPLY_MARKERS)
    best = max(scores, key=lambda key: (scores[key], key == "question"))
    best_score = scores[best]
    runner_up = max((value for key, value in scores.items() if key != best), default=0)
    if best_score == 0:
        return ReplyTriage(intent="question", confidence=0.25,
                           requires_human=True,
                           reason="no clear deterministic signal")
    confidence = min(0.95, 0.55 + 0.12 * best_score - 0.15 * runner_up)
    if best_score == runner_up:
        confidence -= 0.2
    confidence = round(max(0.0, confidence), 3)
    requires_human = confidence < config.REPLY_CONFIDENCE_THRESHOLD
    return ReplyTriage(
        intent=best,
        confidence=confidence,
        requires_human=requires_human,
        reason=("low confidence" if requires_human else "clear deterministic signal"),
    )


def guardrail_callbacks() -> Dict[str, Any]:
    return {
        "before_model_callback": before_model_callback,
        "after_model_callback": after_model_callback,
        "before_tool_callback": before_tool_callback,
        "after_tool_callback": after_tool_callback,
    }


__all__ = [
    "after_model_callback",
    "after_tool_callback",
    "before_model_callback",
    "before_tool_callback",
    "classify_reply",
    "guardrail_callbacks",
    "minimize_pii",
    "normalize_text",
    "validate_tool_arguments",
]
