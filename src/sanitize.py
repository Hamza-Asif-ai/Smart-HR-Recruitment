"""
sanitize.py
===========
Prompt injection defense for untrusted content (resumes, emails).
"""

from __future__ import annotations

import re
from typing import Any


_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?previous\s+instructions?",
    r"system\s*:",
    r"assistant\s*:",
    r"role-play\s*:?",
    r"you\s+are\s+now\s+",
    r"act\s+as\s+",
    r"pretend\s+to\s+be\s+",
]


def sanitize_untrusted_text(text: str) -> str:
    """Wrap untrusted content in tags and strip common injection markers.
    
    Args:
        text: Untrusted text (resume/email content)
        
    Returns:
        Sanitized text wrapped in <untrusted_data> tags
    """
    if not text:
        return "<untrusted_data></untrusted_data>"
    
    # Cap length
    capped = text[:8000]
    
    # Strip common injection markers (case-insensitive)
    sanitized = capped
    for pattern in _INJECTION_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
    
    return f"<untrusted_data>\n{sanitized}\n</untrusted_data>"


__all__ = ["sanitize_untrusted_text"]
