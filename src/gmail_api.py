"""Minimal Gmail REST client backed by OAuth access tokens.

Only read operations are exposed. The app fetches raw RFC 822 messages so the
existing, bounded MIME/PDF attachment parsers can be reused unchanged. No Gmail
send, delete, or modify endpoint exists in this module, which keeps the
screening pipeline read-only even if it is compromised.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Callable, Dict, List, Optional

import requests

from . import config
from .google_oauth import OAuthError, default_oauth


class GmailAPIError(RuntimeError):
    """Gmail API request failed; message is safe to display."""


class GmailMessageTooLarge(GmailAPIError):
    """A raw message exceeded the configured download bound."""


def _decode_raw(payload: str, max_bytes: int) -> bytes:
    if not payload:
        return b""
    try:
        padding = "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload + padding)
    except (binascii.Error, ValueError) as exc:
        raise GmailAPIError("Gmail returned an unreadable message payload.") from exc
    if len(raw) > max_bytes:
        raise GmailMessageTooLarge("Message exceeds the configured size limit.")
    return raw


class GmailClient:
    """Thin, testable Gmail read-only client."""

    def __init__(
        self,
        token_provider: Optional[Callable[[], str]] = None,
        *,
        session: Optional[requests.Session] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        max_message_bytes: Optional[int] = None,
    ):
        self._token_provider = token_provider or (lambda: default_oauth().access_token())
        self.session = session or requests.Session()
        self.base_url = (base_url or config.GMAIL_API_BASE).rstrip("/")
        self.timeout = int(timeout or config.GMAIL_API_TIMEOUT_SECONDS)
        self.max_message_bytes = int(max_message_bytes or config.GMAIL_MAX_MESSAGE_BYTES)

    def _token(self) -> str:
        try:
            token = self._token_provider()
        except OAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak token contents
            raise GmailAPIError(f"Gmail authentication failed ({type(exc).__name__}).") from exc
        if not token:
            raise GmailAPIError("Gmail is not connected.")
        return token

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self.session.get(
                url,
                headers={"Authorization": f"Bearer {self._token()}"},
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GmailAPIError(f"Could not reach Gmail ({type(exc).__name__}).") from exc
        if response.status_code == 401:
            raise GmailAPIError("Gmail authorization expired; reconnect Google OAuth.")
        if response.status_code == 403:
            raise GmailAPIError("Gmail permission denied; check the OAuth scopes.")
        if response.status_code != 200:
            raise GmailAPIError(f"Gmail API returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GmailAPIError("Gmail returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise GmailAPIError("Gmail returned an invalid response.")
        return payload

    def list_message_ids(self, limit: int = 60, query: str = "") -> List[str]:
        """Return up to ``limit`` message ids, newest first, with pagination."""
        wanted = max(1, min(int(limit or 60), 500))
        ids: List[str] = []
        page_token = ""
        while len(ids) < wanted:
            params: Dict[str, Any] = {
                "maxResults": min(500, wanted - len(ids)),
                "includeSpamTrash": "false",
            }
            if query:
                params["q"] = str(query)[:500]
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("users/me/messages", params)
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                break
            for item in messages:
                message_id = str((item or {}).get("id", "") or "")
                if message_id and message_id not in ids:
                    ids.append(message_id)
                if len(ids) >= wanted:
                    break
            page_token = str(payload.get("nextPageToken", "") or "")
            if not page_token:
                break
        return ids

    def get_raw(self, message_id: str) -> bytes:
        """Fetch a raw RFC 822 message, bounded by ``max_message_bytes``."""
        clean = str(message_id or "").strip()
        if not clean or any(ch in clean for ch in "/?#"):
            raise GmailAPIError("Invalid Gmail message id.")
        payload = self._get(f"users/me/messages/{clean}", {"format": "raw"})
        return _decode_raw(str(payload.get("raw", "") or ""), self.max_message_bytes)

    def profile(self) -> Dict[str, Any]:
        """Return the authenticated Gmail profile (without exposing the token)."""
        payload = self._get("users/me/profile")
        return {
            "emailAddress": str(payload.get("emailAddress", "") or ""),
            "messagesTotal": int(payload.get("messagesTotal", 0) or 0),
            "threadsTotal": int(payload.get("threadsTotal", 0) or 0),
        }

    def status(self) -> Dict[str, Any]:
        try:
            profile = self.profile()
            return {"connected": True, "email": profile["emailAddress"]}
        except (GmailAPIError, OAuthError) as exc:
            return {"connected": False, "error": str(exc)}


__all__ = [
    "GmailAPIError",
    "GmailClient",
    "GmailMessageTooLarge",
]
