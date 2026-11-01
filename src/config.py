"""Central configuration for thresholds and interview scheduling.

All values are environment-driven so the deterministic scorer, the agent
instructions, the CLI, the notebook, and the UI share one source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path
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


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 10_000_000) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _env_ratio(name: str, default: float, *, minimum: float = 0.0,
               maximum: float = 1.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


SHORTLIST_THRESHOLD = _env_float("SHORTLIST_THRESHOLD", DEFAULT_SHORTLIST_THRESHOLD)
REVIEW_THRESHOLD = _env_float("REVIEW_THRESHOLD", DEFAULT_REVIEW_THRESHOLD)

if REVIEW_THRESHOLD > SHORTLIST_THRESHOLD:
    raise ValueError("REVIEW_THRESHOLD must not exceed SHORTLIST_THRESHOLD")

INTERVIEW_TIMEZONE = os.environ.get(
    "INTERVIEW_TIMEZONE", DEFAULT_INTERVIEW_TIMEZONE
).strip() or DEFAULT_INTERVIEW_TIMEZONE

# --------------------------------------------------------------------------- #
# Phase 2: application security and external-service limits
# --------------------------------------------------------------------------- #
HR_ALLOW_NO_AUTH = os.environ.get("HR_ALLOW_NO_AUTH", "").strip() == "1"
HR_USERS = os.environ.get("HR_USERS", "").strip()
AUTH_PBKDF2_ITERATIONS = _env_int("HR_AUTH_PBKDF2_ITERATIONS", 260_000,
                                  minimum=100_000, maximum=2_000_000)
AUTH_MAX_FAILURES = _env_int("HR_AUTH_MAX_FAILURES", 5, minimum=1, maximum=50)
AUTH_LOCKOUT_SECONDS = _env_int("HR_AUTH_LOCKOUT_SECONDS", 300, minimum=1, maximum=86_400)
AUTH_AUDIT_LOG = os.environ.get(
    "HR_AUTH_AUDIT_LOG", "data/output/auth_audit.log"
).strip()

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com").strip() or "imap.gmail.com"
IMAP_PORT = _env_int("IMAP_PORT", 993, minimum=1, maximum=65535)
IMAP_TIMEOUT_SECONDS = _env_int("IMAP_TIMEOUT_SECONDS", 20, minimum=1, maximum=300)
IMAP_SCAN_LIMIT = _env_int("IMAP_SCAN_LIMIT", 150, minimum=1, maximum=1000)
IMAP_MAX_MESSAGE_BYTES = _env_int("IMAP_MAX_MESSAGE_BYTES", 20 * 1024 * 1024,
                                  minimum=64 * 1024, maximum=100 * 1024 * 1024)
IMAP_MAX_ATTACHMENT_BYTES = _env_int("IMAP_MAX_ATTACHMENT_BYTES", 10 * 1024 * 1024,
                                     minimum=64 * 1024, maximum=50 * 1024 * 1024)
IMAP_MAX_TEXT_CHARS = _env_int("IMAP_MAX_TEXT_CHARS", 200_000,
                               minimum=1_000, maximum=2_000_000)
IMAP_MAX_PDF_PAGES = _env_int("IMAP_MAX_PDF_PAGES", 50, minimum=1, maximum=500)
DOCX_MAX_UNCOMPRESSED_BYTES = _env_int("DOCX_MAX_UNCOMPRESSED_BYTES", 20 * 1024 * 1024,
                                        minimum=64 * 1024, maximum=200 * 1024 * 1024)
DOCX_MAX_COMPRESSION_RATIO = _env_int("DOCX_MAX_COMPRESSION_RATIO", 100,
                                       minimum=2, maximum=10_000)
DOCX_MAX_XML_BYTES = _env_int("DOCX_MAX_XML_BYTES", 5 * 1024 * 1024,
                               minimum=64 * 1024, maximum=50 * 1024 * 1024)

BREVO_TIMEOUT_SECONDS = _env_int("BREVO_TIMEOUT_SECONDS", 20, minimum=1, maximum=120)
BREVO_BATCH_LIMIT = _env_int("BREVO_BATCH_LIMIT", 50, minimum=1, maximum=500)

QUEUE_MAX_SIZE = _env_int("HR_QUEUE_MAX_SIZE", 32, minimum=1, maximum=10_000)
QUEUE_CONCURRENCY_LIMIT = _env_int("HR_QUEUE_CONCURRENCY_LIMIT", 4, minimum=1, maximum=100)
SERVER_MAX_THREADS = _env_int("HR_SERVER_MAX_THREADS", 40, minimum=1, maximum=500)
STATE_SESSION_CAPACITY = _env_int("HR_STATE_SESSION_CAPACITY", 1000,
                                  minimum=1, maximum=100_000)
TEMP_FILE_MAX_AGE_SECONDS = _env_int("HR_TEMP_FILE_MAX_AGE_SECONDS", 3600,
                                     minimum=60, maximum=86_400)

# --------------------------------------------------------------------------- #
# Phase 3: real agentic architecture
# --------------------------------------------------------------------------- #
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()
MAX_LLM_CALLS = _env_int("MAX_LLM_CALLS", 24, minimum=1, maximum=10_000)
MAX_SECONDS = _env_int("MAX_SECONDS", 300, minimum=1, maximum=86_400)
MAX_TOKENS = _env_int("MAX_TOKENS", 200_000, minimum=1, maximum=100_000_000)

SKILL_DISAGREEMENT_THRESHOLD = _env_ratio("SKILL_DISAGREEMENT_THRESHOLD", 0.35)
REPLY_CONFIDENCE_THRESHOLD = _env_ratio("REPLY_CONFIDENCE_THRESHOLD", 0.65)

MEMORY_DB_PATH = os.environ.get(
    "HR_MEMORY_DB", "data/output/candidate_memory.sqlite3"
).strip()
MEMORY_HMAC_KEY = os.environ.get(
    "HR_MEMORY_HMAC_KEY", os.environ.get("MEMORY_HMAC_KEY", "")
).strip()
APPROVAL_QUEUE_MAX_SIZE = _env_int("HR_APPROVAL_QUEUE_MAX_SIZE", 100,
                                   minimum=1, maximum=10_000)
BLIND_MODE_DEFAULT = os.environ.get("BLIND_MODE", "").strip() == "1"

# --------------------------------------------------------------------------- #
# Phase 5: Gmail OAuth, Calendar availability, and job workspaces
# --------------------------------------------------------------------------- #
_DATA_ROOT = Path(os.environ.get("HR_DATA_ROOT", "./data")).expanduser()

GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REDIRECT_URI = (
    os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8080/oauth2callback").strip()
    or "http://localhost:8080/oauth2callback"
)
GOOGLE_OAUTH_AUTH_URI = os.environ.get(
    "GOOGLE_OAUTH_AUTH_URI", "https://accounts.google.com/o/oauth2/v2/auth"
).strip()
GOOGLE_OAUTH_TOKEN_URI = os.environ.get(
    "GOOGLE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token"
).strip()
GOOGLE_OAUTH_SCOPES = tuple(
    scope for scope in (
        item.strip() for item in os.environ.get(
            "GOOGLE_OAUTH_SCOPES",
            "openid email "
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/calendar.freebusy",
        ).replace(",", " ").split()
    ) if scope
)
GOOGLE_OAUTH_TOKEN_FILE = os.environ.get(
    "GOOGLE_OAUTH_TOKEN_FILE", str(_DATA_ROOT / "output" / "google_oauth_token.json")
).strip()

GMAIL_API_BASE = os.environ.get(
    "GMAIL_API_BASE", "https://gmail.googleapis.com/gmail/v1"
).strip().rstrip("/")
GMAIL_API_TIMEOUT_SECONDS = _env_int("GMAIL_API_TIMEOUT_SECONDS", 20, minimum=1, maximum=120)
GMAIL_MAX_MESSAGE_BYTES = _env_int(
    "GMAIL_MAX_MESSAGE_BYTES", 20 * 1024 * 1024,
    minimum=64 * 1024, maximum=100 * 1024 * 1024,
)

CALENDAR_API_BASE = os.environ.get(
    "CALENDAR_API_BASE", "https://www.googleapis.com/calendar/v3"
).strip().rstrip("/")
CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary").strip() or "primary"
CALENDAR_LOOKAHEAD_DAYS = _env_int("CALENDAR_LOOKAHEAD_DAYS", 14, minimum=1, maximum=60)
INTERVIEW_SLOT_MINUTES = _env_int("INTERVIEW_SLOT_MINUTES", 30, minimum=15, maximum=240)
WORKSPACE_FILE = os.environ.get(
    "HR_WORKSPACE_FILE", str(_DATA_ROOT / "output" / "job_workspaces.json")
).strip()


def get_gemini_api_key() -> str:
    """Return the configured Gemini key from either supported variable."""
    return (os.environ.get("GOOGLE_API_KEY", "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip())


def get_gemini_model() -> str:
    """Return the configured model id; there is deliberately no hardcoded default."""
    return (os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_MODEL).strip()


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
    "HR_ALLOW_NO_AUTH",
    "HR_USERS",
    "AUTH_PBKDF2_ITERATIONS",
    "AUTH_MAX_FAILURES",
    "AUTH_LOCKOUT_SECONDS",
    "AUTH_AUDIT_LOG",
    "IMAP_HOST",
    "IMAP_PORT",
    "IMAP_TIMEOUT_SECONDS",
    "IMAP_SCAN_LIMIT",
    "IMAP_MAX_MESSAGE_BYTES",
    "IMAP_MAX_ATTACHMENT_BYTES",
    "IMAP_MAX_TEXT_CHARS",
    "IMAP_MAX_PDF_PAGES",
    "DOCX_MAX_UNCOMPRESSED_BYTES",
    "DOCX_MAX_COMPRESSION_RATIO",
    "DOCX_MAX_XML_BYTES",
    "BREVO_TIMEOUT_SECONDS",
    "BREVO_BATCH_LIMIT",
    "QUEUE_MAX_SIZE",
    "QUEUE_CONCURRENCY_LIMIT",
    "SERVER_MAX_THREADS",
    "STATE_SESSION_CAPACITY",
    "TEMP_FILE_MAX_AGE_SECONDS",
    "GEMINI_MODEL",
    "MAX_LLM_CALLS",
    "MAX_SECONDS",
    "MAX_TOKENS",
    "SKILL_DISAGREEMENT_THRESHOLD",
    "REPLY_CONFIDENCE_THRESHOLD",
    "MEMORY_DB_PATH",
    "MEMORY_HMAC_KEY",
    "APPROVAL_QUEUE_MAX_SIZE",
    "BLIND_MODE_DEFAULT",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "GOOGLE_OAUTH_AUTH_URI",
    "GOOGLE_OAUTH_TOKEN_URI",
    "GOOGLE_OAUTH_SCOPES",
    "GOOGLE_OAUTH_TOKEN_FILE",
    "GMAIL_API_BASE",
    "GMAIL_API_TIMEOUT_SECONDS",
    "GMAIL_MAX_MESSAGE_BYTES",
    "CALENDAR_API_BASE",
    "CALENDAR_ID",
    "CALENDAR_LOOKAHEAD_DAYS",
    "INTERVIEW_SLOT_MINUTES",
    "WORKSPACE_FILE",
    "get_gemini_api_key",
    "get_gemini_model",
    "get_interview_timezone",
    "get_shortlist_threshold",
    "get_review_threshold",
]
