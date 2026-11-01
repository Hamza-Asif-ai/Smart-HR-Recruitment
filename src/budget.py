"""Per-run budgets for model calls, tokens, steps, and wall-clock time.

A budget belongs to exactly one run/session. It is never a process-wide
singleton, otherwise one browser tab could exhaust another user's allowance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict

from . import config


class BudgetExceededError(RuntimeError):
    """Raised when a run exceeds its step or time budget."""


class LLMBudgetExceeded(BudgetExceededError):
    """Raised before a model call that would exceed the configured limits."""


@dataclass
class BudgetLimits:
    max_llm_calls: int = config.MAX_LLM_CALLS
    max_seconds: float = float(config.MAX_SECONDS)
    max_tokens: int = config.MAX_TOKENS
    max_steps: int = 100

    @classmethod
    def from_env(cls) -> "BudgetLimits":
        return cls(
            max_llm_calls=config.MAX_LLM_CALLS,
            max_seconds=float(config.MAX_SECONDS),
            max_tokens=config.MAX_TOKENS,
        )


@dataclass
class RunBudget:
    """Mutable accounting object scoped to a single run."""

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    model_calls: int = 0
    tokens: int = 0
    steps_used: int = 0
    started_at: float = field(default_factory=time.monotonic)
    degraded: bool = False
    degraded_reason: str = ""

    @property
    def max_steps(self) -> int:
        return self.limits.max_steps

    @property
    def max_seconds(self) -> float:
        return self.limits.max_seconds

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_calls(self) -> int:
        return max(0, self.limits.max_llm_calls - self.model_calls)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.limits.max_tokens - self.tokens)

    def _degrade(self, reason: str) -> None:
        self.degraded = True
        self.degraded_reason = reason

    def _check_time(self) -> None:
        if self.elapsed_seconds > self.limits.max_seconds:
            reason = f"run exceeded MAX_SECONDS={self.limits.max_seconds:g}"
            self._degrade(reason)
            raise BudgetExceededError(reason)

    def consume(self, steps: int = 1) -> None:
        """Account for deterministic work steps and check the time budget."""
        self.steps_used += max(0, int(steps))
        if self.steps_used > self.limits.max_steps:
            reason = f"run exceeded step budget ({self.limits.max_steps})"
            self._degrade(reason)
            raise BudgetExceededError(reason)
        self._check_time()

    def before_model_call(self) -> None:
        """Reserve one real model call or fail with a degradation reason."""
        self._check_time()
        if self.model_calls >= self.limits.max_llm_calls:
            reason = f"run exceeded MAX_LLM_CALLS={self.limits.max_llm_calls}"
            self._degrade(reason)
            raise LLMBudgetExceeded(reason)
        self.model_calls += 1

    def record_tokens(self, token_count: Any = 0) -> None:
        """Record usage from ``usage_metadata`` after a model response."""
        try:
            count = max(0, int(token_count or 0))
        except (TypeError, ValueError):
            count = 0
        self.tokens += count
        if self.tokens > self.limits.max_tokens:
            self._degrade(f"run exceeded MAX_TOKENS={self.limits.max_tokens}")

    def ensure_llm_available(self) -> None:
        """Called before an LLM-backed path; refuses after degradation."""
        if self.degraded:
            raise LLMBudgetExceeded(self.degraded_reason or "LLM budget exhausted")
        self._check_time()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "max_llm_calls": self.limits.max_llm_calls,
            "max_seconds": self.limits.max_seconds,
            "max_tokens": self.limits.max_tokens,
            "max_steps": self.limits.max_steps,
            "model_calls": self.model_calls,
            "tokens": self.tokens,
            "steps_used": self.steps_used,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }

    def banner(self) -> str:
        if not self.degraded:
            return ""
        return (
            "Agentic mode degraded to deterministic mode: "
            f"{self.degraded_reason}. Results are still available but no further "
            "model calls will be made."
        )


def usage_token_count(usage_metadata: Any) -> int:
    """Extract total token count from a GenAI/ADK usage object or mapping."""
    if usage_metadata is None:
        return 0
    for name in ("total_token_count", "total_tokens", "totalTokenCount"):
        value = usage_metadata.get(name) if isinstance(usage_metadata, dict) else getattr(
            usage_metadata, name, None
        )
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0
    return 0


__all__ = [
    "BudgetExceededError",
    "BudgetLimits",
    "LLMBudgetExceeded",
    "RunBudget",
    "usage_token_count",
]
