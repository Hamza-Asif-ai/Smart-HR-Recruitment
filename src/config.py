"""Central configuration for thresholds and interview scheduling.

All values are environment-driven so the deterministic scorer, the agent
instructions, the CLI, the notebook, and the UI share one source of truth.
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_SHORTLIST_THRESHOLD = 75.0
DEFAULT_REVIEW_THRESHOLD = 55.0
DEFAULT_INTERVIEW_TIMEZONE = "Asia/Kolkata"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100, got {value}")
    return value


SHORTLIST_THRESHOLD = _env_float("SHORTLIST_THRESHOLD", DEFAULT_SHORTLIST_THRESHOLD)
REVIEW_THRESHOLD = _env_float("REVIEW_THRESHOLD", DEFAULT_REVIEW_THRESHOLD)

if REVIEW_THRESHOLD > SHORTLIST_THRESHOLD:
    raise ValueError("REVIEW_THRESHOLD must not exceed SHORTLIST_THRESHOLD")

INTERVIEW_TIMEZONE = os.environ.get(
    "INTERVIEW_TIMEZONE", DEFAULT_INTERVIEW_TIMEZONE
).strip() or DEFAULT_INTERVIEW_TIMEZONE


def get_interview_timezone() -> ZoneInfo:
    """Return the configured IANA timezone, falling back to UTC if unavailable."""
    try:
        return ZoneInfo(INTERVIEW_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def get_shortlist_threshold() -> float:
    return SHORTLIST_THRESHOLD


def get_review_threshold() -> float:
    return REVIEW_THRESHOLD


__all__ = [
    "DEFAULT_SHORTLIST_THRESHOLD",
    "DEFAULT_REVIEW_THRESHOLD",
    "DEFAULT_INTERVIEW_TIMEZONE",
    "SHORTLIST_THRESHOLD",
    "REVIEW_THRESHOLD",
    "INTERVIEW_TIMEZONE",
    "get_interview_timezone",
    "get_shortlist_threshold",
    "get_review_threshold",
]
