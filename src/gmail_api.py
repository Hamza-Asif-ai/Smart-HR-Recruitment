"""Minimal read-only Gmail API client used by the inbox UI.

Only ``gmail.readonly`` is required: we list message ids and download raw
RFC822 bytes, then reuse the existing MIME/attachment parsing pipeline. Tokens
are supplied by a callable and are never written to logs or traces.
"""

from __future__ import annotations

import base64
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

import requests

from . import config


class GmailAPIError(RuntimeError):
    """A safe-to-display Gmail API failure."""


class GmailInbox:
    def __init__(
        self,
        access_token_provider: Callable[[], Optional[str]],
        *,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        session: Optional[requests.Session] = None,
        max_message_bytes: Optional[int] = None,
    ):
        self.token_provider = access_token_provider
        self.base_url = (base_url or config.GMAIL_API_BASE).rstrip("/")
        self.timeout = int(timeout or config.GMAIL_API_TIMEOUT_SECONDS)
        self.session = session or requests
        self.max_message_bytes = int(
            max_message_bytes or config.GMAIL_MAX_MESSAGE_BYTES
        )

    def _headers(self) -> Dict[str, str]:
        token = self.token_provider()
        if not token:
            raise GmailAPIError("Gmail is not connected. Sign in with Google first.")
        return {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, params: Optional[Dict[str, object]] = None) -> Dict:
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                headers=self._headers(),
                params=params or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GmailAPIError(
                f"Could not reach Gmail ({type(exc).__name__})."
            ) from exc
        if response.status_code == 401:
            raise GmailAPIError("Gmail access expired. Sign in with Google again.")
        if response.status_code == 403:
            raise GmailAPIError(
                "Gmail permission denied. Check the gmail.readonly scope."
            )
        if response.status_code == 429:
            raise GmailAPIError("Gmail rate limit reached. Try again shortly.")
        if response.status_code >= 400:
            raise GmailAPIError(
                f"Gmail request failed (HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GmailAPIError("Gmail returned an unreadable response.") from exc
        if not isinstance(payload, dict):
            raise GmailAPIError("Gmail returned an unexpected response.")
        return payload

    def message_ids(self, limit: int) -> List[str]:
        """Return the newest ``limit`` message ids, oldest first."""
        remaining = max(0, int(limit))
        if remaining == 0:
            return []
        ids: List[str] = []
        page_token = ""
        while remaining > 0:
            params: Dict[str, object] = {
                "maxResults": min(remaining, 500),
                "includeSpamTrash": "false",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("/users/me/messages", params)
            for item in payload.get("messages", []) or []:
                if isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]))
                    remaining -= 1
                    if remaining <= 0:
                        break
            page_token = str(payload.get("nextPageToken", "") or "")
            if not page_token:
                break
        return list(reversed(ids))

    def fetch_raw(self, message_id: str) -> Optional[bytes]:
        """Download one message as raw RFC822 bytes, or ``None`` if too large."""
        if not message_id:
            return None
        payload = self._get(
            f"/users/me/messages/{quote(str(message_id), safe='')}",
            {"format": "raw"},
        )
        raw = payload.get("raw")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            padding = "=" * (-len(raw) % 4)
            data = base64.urlsafe_b64decode(raw + padding)
        except (ValueError, TypeError):
            return None
        if len(data) > self.max_message_bytes:
            return None
        return data


__all__ = ["GmailAPIError", "GmailInbox"]
