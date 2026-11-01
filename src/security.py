"""
security.py
===========
Security layer for the Smart HR Recruitment system.

Provides two capabilities required by the capstone:

1. PII MASKING  - Detect and mask Personally Identifiable Information
                  (emails, phone numbers, names, URLs) before anything is
                  written to logs or non-privileged outputs.

2. RBAC         - Role-Based Access Control. Each agent/tool action is gated
                  by the caller's role so that, e.g., only a `recruiter` can
                  view unmasked candidate contact details.

The module is intentionally dependency-free so it runs unchanged on Kaggle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable


# --------------------------------------------------------------------------- #
# PII masking
# --------------------------------------------------------------------------- #

# Pre-compiled patterns for common PII. Order matters: emails before URLs and
# phones, because an email can contain digit sequences that look phone-like.
_PII_PATTERNS: Dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "URL": re.compile(
        r"\b(?:https?://|www\.)\S+"
        r"|\b(?:[A-Za-z0-9-]+\.)+(?:com|io|dev|ai|me|co|net|org|in|uk|sg|app|tech|xyz|site)"
        r"(?:/\S*)?",
        re.IGNORECASE,
    ),
    "TRACEBACK": re.compile(r"Traceback \(most recent call last\):.*", re.IGNORECASE | re.DOTALL),
    "TRACEBACK_FRAME": re.compile(
        r'File\s+"[^"]+",\s+line\s+\d+(?:,\s+in\s+[^\n]+)?',
        re.IGNORECASE,
    ),
    "WINDOWS_PATH": re.compile(r"\b[A-Za-z]:\\[^\s\"']+"),
    "UNIX_HOME_PATH": re.compile(
        r"(?<![\w:])/(?:home|Users)/[^/\s\"']+(?:/[^\s\"']*)?"
    ),
    "PHONE": re.compile(
        r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?"
        r"(?:\(?\d{1,5}\)?[\s.-]?){2,3}\d{2,5}(?![\w])"
    ),
}

_YEAR_RANGE_ONLY_RE = re.compile(
    r"^(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}$"
)


def _looks_like_phone(value: str) -> bool:
    """Reject years and plain digit runs so the phone rule only masks phones."""
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        return False
    if _YEAR_RANGE_ONLY_RE.fullmatch(value.strip()):
        return False
    # Require an international prefix or phone punctuation; bare numbers stay.
    return value.lstrip().startswith("+") or bool(re.search(r"[().\s-]", value))


def _mask_phone(match: re.Match) -> str:
    value = match.group(0)
    return "[PHONE]" if _looks_like_phone(value) else value


def mask_pii(text: str, mask_names: bool = False) -> str:
    """Return ``text`` with PII replaced by typed placeholders.

    Args:
        text: Free-form text that may contain PII.
        mask_names: If True, also mask the very first ALL-CAPS / Title line,
            which in a resume is almost always the candidate's name.
    """
    if not text:
        return text

    masked = text

    # Mask the candidate name (usually the first non-empty line of a resume).
    if mask_names:
        lines = masked.splitlines()
        for i, line in enumerate(lines):
            if line.strip():
                lines[i] = "[NAME]"
                break
        masked = "\n".join(lines)

    masked = _PII_PATTERNS["TRACEBACK"].sub("[TRACEBACK]", masked)
    masked = _PII_PATTERNS["TRACEBACK_FRAME"].sub("[TRACEBACK]", masked)
    masked = _PII_PATTERNS["EMAIL"].sub("[EMAIL]", masked)
    masked = _PII_PATTERNS["URL"].sub("[URL]", masked)
    masked = _PII_PATTERNS["WINDOWS_PATH"].sub("[PATH]", masked)
    masked = _PII_PATTERNS["UNIX_HOME_PATH"].sub("[PATH]", masked)
    masked = _PII_PATTERNS["PHONE"].sub(_mask_phone, masked)
    return masked


# --------------------------------------------------------------------------- #
# Secrets and email-header safety
# --------------------------------------------------------------------------- #
_SECRET_PATTERNS = [
    re.compile(r"xkeysib-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
]

_STRICT_EMAIL_RE = re.compile(
    r"^(?P<local>[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64})@"
    r"(?P<domain>[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)$"
)


def has_header_injection(value: Any) -> bool:
    """True if a value contains CR/LF or NUL and cannot be used in a header."""
    text = "" if value is None else str(value)
    return bool(re.search(r"[\r\n\x00]", text))


def is_valid_email(value: Any) -> bool:
    """Strict single-address validation for outbound recipient/sender fields."""
    if value is None:
        return False
    text = str(value).strip()
    if not text or len(text) > 254 or has_header_injection(text):
        return False
    if text.count("@") != 1 or ".." in text:
        return False
    match = _STRICT_EMAIL_RE.fullmatch(text)
    return bool(match)


def scrub_secrets(text: Any, secrets: Iterable[str] = ()) -> str:
    """Remove known secret values and common credential patterns from text."""
    cleaned = "" if text is None else str(text)
    for secret in secrets:
        if secret is None:
            continue
        value = str(secret)
        if value:
            cleaned = cleaned.replace(value, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def safe_error(exc: BaseException, secrets: Iterable[str] = ()) -> str:
    """Exception text with secrets (and optionally PII) removed for display/logging."""
    return mask_pii(scrub_secrets(str(exc), secrets))


def safe_preview(text: str, limit: int = 60, mask_names: bool = False) -> str:
    """Mask PII *before* truncating, so a cut-off email/phone cannot leak.

    Truncating first (``mask_pii(text[:limit])``) can leave a partial address
    such as ``nadia.petrova@exampl`` in a log line, because the regex no longer
    sees a complete email. This helper always masks the whole string first.

    Set ``mask_names=True`` for resume previews, where the first non-empty line
    is normally the candidate's name.
    """
    if not text:
        return ""
    return mask_pii(str(text), mask_names=mask_names)[:limit]


def mask_pii_in_obj(obj: Any) -> Any:
    """Recursively mask PII in strings inside dicts/lists (for safe logging)."""
    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, dict):
        return {k: mask_pii_in_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(mask_pii_in_obj(v) for v in obj)
    return obj


class PiiMaskingFilter(logging.Filter):
    """Logging filter that masks PII in every log record's message.

    Attach this to any handler/logger to guarantee that no email, phone, or
    URL is ever persisted to disk or the console in clear text.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = mask_pii(str(record.getMessage()))
            record.args = ()  # already interpolated into msg
        except Exception:  # never let logging crash the pipeline
            pass
        return True


def get_secure_logger(name: str = "smart_hr") -> logging.Logger:
    """Return a logger whose output is automatically PII-masked."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        handler.addFilter(PiiMaskingFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


# --------------------------------------------------------------------------- #
# Role-Based Access Control (RBAC)
# --------------------------------------------------------------------------- #

class Role(str, Enum):
    """Roles recognised by the recruitment system."""

    ADMIN = "admin"            # full access, can manage the pipeline
    RECRUITER = "recruiter"    # can view unmasked PII, schedule interviews
    HIRING_MANAGER = "hiring_manager"  # can view scores, masked PII only
    VIEWER = "viewer"          # read-only, fully masked


# Capability -> set of roles allowed to perform it.
_PERMISSIONS: Dict[str, set] = {
    "read_resume": {Role.ADMIN, Role.RECRUITER, Role.HIRING_MANAGER},
    "view_unmasked_pii": {Role.ADMIN, Role.RECRUITER},
    "score_candidates": {Role.ADMIN, Role.RECRUITER, Role.HIRING_MANAGER},
    "send_invitations": {Role.ADMIN, Role.RECRUITER},
    "write_report": {Role.ADMIN, Role.RECRUITER},
    "view_report": {Role.ADMIN, Role.RECRUITER, Role.HIRING_MANAGER, Role.VIEWER},
}


class AccessDeniedError(PermissionError):
    """Raised when a role attempts an action it is not permitted to perform."""


@dataclass
class SecurityContext:
    """Represents the identity performing an action within the pipeline."""

    user: str = "system"
    role: Role = Role.VIEWER
    audit_log: list = field(default_factory=list)

    def can(self, capability: str) -> bool:
        allowed = _PERMISSIONS.get(capability, set())
        return self.role in allowed

    def authorize(self, capability: str) -> None:
        """Raise AccessDeniedError if the current role lacks ``capability``."""
        granted = self.can(capability)
        self.audit_log.append(
            {"user": self.user, "role": self.role.value, "capability": capability, "granted": granted}
        )
        if not granted:
            raise AccessDeniedError(
                f"Role '{self.role.value}' is not permitted to '{capability}'."
            )

    def maybe_mask(self, text: str) -> str:
        """Mask PII unless the role is explicitly allowed to see it."""
        return text if self.can("view_unmasked_pii") else mask_pii(text)


def require(capability: str):
    """Decorator factory: gate a function behind an RBAC capability.

    The decorated function must receive a ``ctx: SecurityContext`` keyword
    argument (or as the first positional argument).
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            ctx = kwargs.get("ctx")
            if ctx is None:
                ctx = next((a for a in args if isinstance(a, SecurityContext)), None)
            if ctx is None:
                raise AccessDeniedError("No SecurityContext supplied for a protected action.")
            ctx.authorize(capability)
            return func(*args, **kwargs)

        wrapper.__name__ = getattr(func, "__name__", "wrapped")
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


__all__ = [
    "AccessDeniedError",
    "PiiMaskingFilter",
    "Role",
    "SecurityContext",
    "get_secure_logger",
    "has_header_injection",
    "is_valid_email",
    "mask_pii",
    "mask_pii_in_obj",
    "require",
    "safe_error",
    "safe_preview",
    "scrub_secrets",
]
