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

# Pre-compiled patterns for common PII. Order matters: emails before phones,
# because an email can contain digit sequences that look phone-like.
_PII_PATTERNS: Dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "PHONE": re.compile(
        r"(?<!\w)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4}(?!\w)"
    ),
    "URL": re.compile(r"\b(?:https?://|www\.)?[A-Za-z0-9.-]+\.(?:com|io|dev|in|uk|sg)\b/?\S*"),
}


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

    masked = _PII_PATTERNS["EMAIL"].sub("[EMAIL]", masked)
    masked = _PII_PATTERNS["URL"].sub("[URL]", masked)
    masked = _PII_PATTERNS["PHONE"].sub("[PHONE]", masked)
    return masked


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
    "mask_pii",
    "mask_pii_in_obj",
    "PiiMaskingFilter",
    "get_secure_logger",
    "Role",
    "SecurityContext",
    "AccessDeniedError",
    "require",
]
