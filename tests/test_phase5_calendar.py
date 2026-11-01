"""Phase 5: Google Calendar free/busy lookups and interview .ics generation."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from src import calendar_api, config


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _client(response: FakeResponse) -> tuple[calendar_api.CalendarClient, FakeSession]:
    session = FakeSession(response)
    client = calendar_api.CalendarClient(
        token_provider=lambda: "test-token",
        session=session,
        base_url="https://calendar.example.test",
        timeout=5,
    )
    return client, session


def test_free_busy_sends_read_only_request_and_parses_periods():
    payload = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2026-01-05T10:00:00Z", "end": "2026-01-05T10:30:00Z"},
                    {"start": "2026-01-05T09:00:00Z", "end": "2026-01-05T09:30:00Z"},
                ]
            }
        }
    }
    client, session = _client(FakeResponse(200, payload))
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    periods = client.free_busy(start, start + timedelta(days=1))

    assert [period.start.hour for period in periods] == [9, 10]
    url, kwargs = session.calls[0]
    assert url == "https://calendar.example.test/freeBusy"
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["json"]["items"] == [{"id": config.CALENDAR_ID}]
    assert "freeBusy" in url


def test_free_busy_rejects_empty_window_and_maps_http_errors():
    client, _ = _client(FakeResponse(200, {}))
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    with pytest.raises(calendar_api.CalendarAPIError):
        client.free_busy(start, start)

    unauthorized, _ = _client(FakeResponse(401, {}))
    with pytest.raises(calendar_api.CalendarAPIError, match="expired"):
        unauthorized.free_busy(start, start + timedelta(hours=1))

    forbidden, _ = _client(FakeResponse(403, {}))
    with pytest.raises(calendar_api.CalendarAPIError, match="permission"):
        forbidden.free_busy(start, start + timedelta(hours=1))


def test_busy_period_overlap_is_half_open():
    start = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    period = calendar_api.BusyPeriod(start, start + timedelta(minutes=30))
    assert period.overlaps(start + timedelta(minutes=15), start + timedelta(minutes=45))
    assert not period.overlaps(start + timedelta(minutes=30), start + timedelta(minutes=60))
    assert not period.overlaps(start - timedelta(minutes=30), start)


def test_find_available_slots_returns_business_hours():
    client, _ = _client(FakeResponse(200, {"calendars": {"primary": {"busy": []}}}))
    slots = client.find_available_slots(2, hours=(10,))
    assert len(slots) == 2
    assert all(slot.weekday() < 5 for slot in slots)
    assert all((slot.hour, slot.minute) == (10, 0) for slot in slots)
    assert all(slot.tzinfo is not None for slot in slots)


def test_find_available_slots_raises_when_everything_is_busy():
    client, _ = _client(FakeResponse(200, {}))
    now = datetime.now(timezone.utc)
    client.free_busy = lambda *args, **kwargs: [  # type: ignore[method-assign]
        calendar_api.BusyPeriod(now - timedelta(days=1), now + timedelta(days=120))
    ]
    with pytest.raises(calendar_api.CalendarNoSlots):
        client.find_available_slots(1, hours=(10,))

    assert client.find_available_slots(0) == []


def test_generate_interview_ics_is_importable_and_bounded():
    start = datetime(2026, 2, 3, 10, 0, tzinfo=config.get_interview_timezone())
    ics = calendar_api.generate_interview_ics(
        candidate_name="Nadia Petrova",
        candidate_email="nadia@example.com",
        position="Backend, Engineer",
        company="Acme; Ltd",
        start=start,
        duration_minutes=45,
        organizer_email="hr@example.com",
        uid="fixed-uid@example.com",
    )

    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert "UID:fixed-uid@example.com" in ics
    assert "DTSTART:20260203T043000Z" in ics
    assert "DTEND:20260203T051500Z" in ics
    assert "SUMMARY:Interview - Backend\\, Engineer at Acme\\; Ltd" in ics
    assert "ATTENDEE;CN=Nadia Petrova;RSVP=TRUE:mailto:nadia@example.com" in ics
    assert all(len(line) <= 75 for line in ics.split("\r\n"))


def test_ics_escaping_handles_newlines_and_backslashes():
    assert calendar_api._ics_escape("a\\b,c;d\r\ne") == "a\\\\b\\,c\\;d\\ne"
    assert calendar_api._fold_ics_line("x" * 80).split("\r\n") == [
        "x" * 75,
        " " + "x" * 5,
    ]


def test_ics_uid_changes_between_calls_by_default():
    start = datetime(2026, 2, 3, 10, 0, tzinfo=timezone.utc)
    kwargs = dict(
        candidate_name="Nadia Petrova",
        candidate_email="nadia@example.com",
        position="Backend Engineer",
        start=start,
    )
    first = calendar_api.generate_interview_ics(**kwargs)
    second = calendar_api.generate_interview_ics(**kwargs)
    assert first != second
    expected = "fixed@example.com"
    assert expected in calendar_api.generate_interview_ics(uid=expected, **kwargs)


def test_pkce_challenge_matches_verifier():
    from urllib.parse import parse_qs, urlparse

    from src import google_oauth

    cfg = google_oauth.OAuthConfig(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost:8080/oauth2callback",
    )
    url, state, verifier = google_oauth.build_authorization_url(cfg)
    params = parse_qs(urlparse(url).query)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert params["state"] == [state]
    assert params["code_challenge"] == [challenge]
    assert params["code_challenge_method"] == ["S256"]
