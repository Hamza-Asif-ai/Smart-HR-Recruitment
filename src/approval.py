"""Human approval queue for outbound email.

No LLM tool can send mail. The email writer produces draft-only output; drafts
enter this queue, the HR previews/edits/approves them, and only a deterministic
mailer may then hand an approved draft to the transport.
"""

from __future__ import annotations

import html
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import config


class ApprovalRequiredError(RuntimeError):
    """Raised when code tries to send a draft that a human has not approved."""


@dataclass
class PendingEmail:
    to: str
    subject: str
    body: str
    candidate: str = ""
    html: str = ""
    ics_content: str = ""
    ics_filename: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    approved: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    edited: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "to": self.to,
            "subject": self.subject,
            "body": self.body,
            "candidate": self.candidate,
            "html": self.html,
            "ics_content": self.ics_content,
            "ics_filename": self.ics_filename,
            "approved": self.approved,
            "edited": self.edited,
            "created_at": self.created_at,
        }


class ApprovalQueue:
    """Thread-safe in-memory queue; never callable by an LLM tool."""

    def __init__(self, max_size: Optional[int] = None):
        self.max_size = max(1, int(max_size or config.APPROVAL_QUEUE_MAX_SIZE))
        self._items: Dict[str, PendingEmail] = {}
        self._lock = threading.Lock()

    def stage(self, draft: Any) -> PendingEmail:
        if isinstance(draft, PendingEmail):
            item = draft
        elif isinstance(draft, dict):
            item = PendingEmail(
                to=str(draft.get("to", "")),
                subject=str(draft.get("subject", "")),
                body=str(draft.get("body", "")),
                candidate=str(draft.get("candidate", "")),
                html=str(draft.get("html", "")),
                ics_content=str(draft.get("ics_content", "")),
                ics_filename=str(draft.get("ics_filename", "")),
            )
        else:
            raise TypeError("draft must be a mapping or PendingEmail")
        with self._lock:
            if len(self._items) >= self.max_size:
                raise ValueError("approval queue is full")
            self._items[item.id] = item
        return item

    def stage_many(self, drafts: Iterable[Any]) -> List[PendingEmail]:
        return [self.stage(draft) for draft in drafts]

    def list(self) -> List[PendingEmail]:
        with self._lock:
            return list(self._items.values())

    def get(self, draft_id: str) -> Optional[PendingEmail]:
        with self._lock:
            return self._items.get(str(draft_id))

    def update_body(self, draft_id: str, body: str, *, subject: Optional[str] = None) -> PendingEmail:
        with self._lock:
            item = self._items.get(str(draft_id))
            if item is None:
                raise KeyError("draft not found")
            if item.approved:
                raise ApprovalRequiredError("cannot edit an already-approved draft")
            item.body = str(body)
            if subject is not None:
                item.subject = str(subject)
            item.edited = True
            return item

    def approve(self, draft_id: str) -> PendingEmail:
        with self._lock:
            item = self._items.get(str(draft_id))
            if item is None:
                raise KeyError("draft not found")
            item.approved = True
            return item

    def reject(self, draft_id: str) -> bool:
        with self._lock:
            return self._items.pop(str(draft_id), None) is not None

    def remove_approved(self, draft_id: str) -> Optional[PendingEmail]:
        with self._lock:
            item = self._items.get(str(draft_id))
            if item is None or not item.approved:
                return None
            return self._items.pop(str(draft_id), None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def preview_html(self, draft_id: Optional[str] = None) -> str:
        items = [self.get(draft_id)] if draft_id else self.list()
        cards = []
        for item in items:
            if item is None:
                continue
            status = "approved" if item.approved else "awaiting approval"
            cards.append(
                '<div class="reply-card">'
                f'<span class="reply-date">{html.escape(item.created_at)} · {status}</span>'
                f'<div class="email-to">{html.escape(item.candidate or item.to)}</div>'
                f'<div class="email-addr">{html.escape(item.to)}</div>'
                f'<div class="email-subject">✉ {html.escape(item.subject)}</div>'
                f'<div class="email-body" style="white-space:pre-wrap;">{html.escape(item.body)}</div>'
                '</div>'
            )
        return "".join(cards)


def send_approved_draft(
    draft: PendingEmail,
    *,
    sender_email: str,
    sender_name: str,
    api_key: str,
    send_func: Callable[..., Any],
) -> Any:
    """Deterministic mailer gate: refuses anything a human has not approved."""
    if not isinstance(draft, PendingEmail) or not draft.approved:
        raise ApprovalRequiredError("draft must be approved by a human before sending")
    payload = {
        "to": draft.to,
        "subject": draft.subject,
        "body": draft.body,
        "candidate": draft.candidate,
        "html": draft.html,
        "ics_content": draft.ics_content,
        "ics_filename": draft.ics_filename,
    }
    return send_func(sender_email, sender_name, api_key, [payload])


__all__ = [
    "ApprovalQueue",
    "ApprovalRequiredError",
    "PendingEmail",
    "send_approved_draft",
]
