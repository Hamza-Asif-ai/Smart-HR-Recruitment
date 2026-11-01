"""Phase 5: read-only Gmail API client."""

from __future__ import annotations

import base64

import pytest

from src.gmail_api import GmailAPIError, GmailClient, GmailMessageTooLarge
from src.google_oauth import OAuthNotConfigured


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _client(responses, *, max_bytes: int = 1024) -> tuple[GmailClient, FakeSession]:
    session = FakeSession(responses)
    client = GmailClient(
        token_provider=lambda: "test-token",
        session=session,
        base_url="https://gmail.example.test",
        timeout=5,
        max_message_bytes=max_bytes,
    )
    return client, session


def test_list_message_ids_paginates_and_deduplicates():
    client, session = _client([
        FakeResponse(200, {
            "messages": [{"id": "a"}, {"id": "b"}],
            "nextPageToken": "page-2",
        }),
        FakeResponse(200, {"messages": [{"id": "c"}, {"id": "a"}]}),
    ])
    assert client.list_message_ids(limit=5) == ["a", "b", "c"]
    assert session.calls[0][0] == "https://gmail.example.test/users/me/messages"
    assert session.calls[0][1]["params"]["maxResults"] == 5
    assert session.calls[1][1]["params"]["pageToken"] == "page-2"


def test_list_message_ids_uses_query_and_caps_limit():
    client, session = _client([FakeResponse(200, {"messages": []})])
    assert client.list_message_ids(limit=9_999, query="subject:application") == []
    assert session.calls[0][1]["params"]["maxResults"] == 500
    assert session.calls[0][1]["params"]["q"] == "subject:application"


def test_get_raw_decodes_urlsafe_base64():
    raw = b"Subject: Application\r\n\r\nHello"
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    client, _ = _client([FakeResponse(200, {"raw": encoded})])
    assert client.get_raw("message-1") == raw


def test_get_raw_rejects_bad_ids_and_oversized_messages():
    client, _ = _client([FakeResponse(200, {"raw": ""})])
    for bad in ("", "a/b", "a?b", "a#b"):
        with pytest.raises(GmailAPIError, match="Invalid Gmail message id"):
            client.get_raw(bad)

    raw = b"x" * 50
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    small, _ = _client([FakeResponse(200, {"raw": encoded})], max_bytes=10)
    with pytest.raises(GmailMessageTooLarge):
        small.get_raw("message-1")


def test_http_errors_are_safe_messages():
    for status, match in ((401, "expired"), (403, "permission"), (500, "HTTP 500")):
        client, _ = _client([FakeResponse(status, {})])
        with pytest.raises(GmailAPIError, match=match):
            client.profile()


def test_profile_returns_counts_without_token():
    client, session = _client([FakeResponse(200, {
        "emailAddress": "hr@example.com",
        "messagesTotal": 12,
        "threadsTotal": 7,
    })])
    assert client.profile() == {
        "emailAddress": "hr@example.com",
        "messagesTotal": 12,
        "threadsTotal": 7,
    }
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer test-token"


def test_status_handles_missing_connection():
    def no_token():
        raise OAuthNotConfigured("Gmail is not connected")

    client = GmailClient(token_provider=no_token, session=FakeSession([]))
    assert client.status() == {"connected": False, "error": "Gmail is not connected"}


def test_invalid_token_provider_is_wrapped_without_leaking_details():
    def broken():
        raise RuntimeError("secret-token-value")

    client = GmailClient(token_provider=broken, session=FakeSession([]))
    with pytest.raises(GmailAPIError, match="RuntimeError") as excinfo:
        client.profile()
    assert "secret-token-value" not in str(excinfo.value)
