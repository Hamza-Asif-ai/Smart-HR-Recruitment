"""Google Calendar free/busy lookups and interview ``.ics`` generation.

The calendar client is read-only: it only calls the FreeBusy endpoint. It never
creates, edits, or deletes calendar events. Invitations are delivered as
attachments generated locally with :func:`generate_interview_ics`, so the app
does not need the calendar read/write scope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, List, Optional, Sequence

import requests

from . import config
from .google_oauth import OAuthError, default_oauth


class CalendarAPIError(RuntimeError):
    """Calendar request failed; the message is safe to display."""


class CalendarNoSlots(CalendarAPIError):
    """No free interview slot was found inside the lookahead window."""


def _ensure_aware(value: datetime, tzinfo=None) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tzinfo or config.get_interview_timezone())
    return value


def _parse_rfc3339(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _ensure_aware(parsed, timezone.utc)


@dataclass(frozen=True)
class BusyPeriod:
    start: datetime
    end: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return start < self.end and end > self.start


class CalendarClient:
    """Thin, testable Google Calendar FreeBusy client."""

    def __init__(
        self,
        token_provider: Optional[Callable[[], str]] = None,
        *,
        session: Optional[requests.Session] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self._token_provider = token_provider or (lambda: default_oauth().access_token())
        self.session = session or requests.Session()
        self.base_url = (base_url or config.CALENDAR_API_BASE).rstrip("/")
        self.timeout = int(timeout or config.GMAIL_API_TIMEOUT_SECONDS)

    def _token(self) -> str:
        try:
            token = self._token_provider()
        except OAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak token contents
            raise CalendarAPIError(
                f"Google Calendar authentication failed ({type(exc).__name__})."
            ) from exc
        if not token:
            raise CalendarAPIError("Google Calendar is not connected.")
        return token

    def free_busy(
        self,
        time_min: datetime,
        time_max: datetime,
        *,
        calendar_id: Optional[str] = None,
        timezone_name: Optional[str] = None,
    ) -> List[BusyPeriod]:
        """Return busy periods for ``calendar_id`` inside the given window."""
        tz = timezone_name or config.INTERVIEW_TIMEZONE
        start = _ensure_aware(time_min, config.get_interview_timezone())
        end = _ensure_aware(time_max, config.get_interview_timezone())
        if end <= start:
            raise CalendarAPIError("The free/busy window is empty.")
        payload = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": tz,
            "items": [{"id": calendar_id or config.CALENDAR_ID}],
        }
        url = f"{self.base_url}/freeBusy"
        try:
            response = self.session.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CalendarAPIError(
                f"Could not reach Google Calendar ({type(exc).__name__})."
            ) from exc
        if response.status_code == 401:
            raise CalendarAPIError("Calendar authorization expired; reconnect Google OAuth.")
        if response.status_code == 403:
            raise CalendarAPIError("Calendar permission denied; check the OAuth scopes.")
        if response.status_code != 200:
            raise CalendarAPIError(f"Calendar API returned HTTP {response.status_code}.")
        try:
            body = response.json()
        except ValueError as exc:
            raise CalendarAPIError("Calendar returned an invalid response.") from exc
        calendars = (body.get("calendars") or {}) if isinstance(body, dict) else {}
        entry = calendars.get(calendar_id or config.CALENDAR_ID) or {}
        busy = entry.get("busy") or []
        periods: List[BusyPeriod] = []
        for item in busy:
            if not isinstance(item, dict):
                continue
            start_at = _parse_rfc3339(item.get("start", ""))
            end_at = _parse_rfc3339(item.get("end", ""))
            if start_at and end_at and end_at > start_at:
                periods.append(BusyPeriod(start_at, end_at))
        periods.sort(key=lambda period: period.start)
        return periods

    def find_available_slots(
        self,
        count: int,
        *,
        start_in_days: int = 3,
        slot_minutes: Optional[int] = None,
        timezone_name: Optional[str] = None,
        calendar_id: Optional[str] = None,
        hours: Sequence[int] = (10, 14, 16),
    ) -> List[datetime]:
        """Return up to ``count`` free business-hour slots."""
        wanted = max(0, int(count))
        if wanted == 0:
            return []
        tz_name = timezone_name or config.INTERVIEW_TIMEZONE
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz = timezone.utc
            tz_name = "UTC"
        duration = timedelta(minutes=int(slot_minutes or config.INTERVIEW_SLOT_MINUTES))

        first_day = datetime.now(tz).date() + timedelta(days=max(0, int(start_in_days)))
        last_day = first_day + timedelta(days=max(1, int(config.CALENDAR_LOOKAHEAD_DAYS)))
        window_start = datetime.combine(first_day, time(0, 0), tzinfo=tz)
        window_end = datetime.combine(last_day, time(23, 59), tzinfo=tz)
        busy = self.free_busy(
            window_start,
            window_end,
            calendar_id=calendar_id,
            timezone_name=tz_name,
        )

        slots: List[datetime] = []
        day: date = first_day
        while day <= last_day and len(slots) < wanted:
            if day.weekday() < 5:
                for hour in hours:
                    if len(slots) >= wanted:
                        break
                    start = datetime.combine(day, time(int(hour), 0), tzinfo=tz)
                    end = start + duration
                    if not any(period.overlaps(start, end) for period in busy):
                        slots.append(start)
            day += timedelta(days=1)
        if not slots:
            raise CalendarNoSlots("No free interview slot was found in the lookahead window.")
        return slots


def _ics_escape(value: str) -> str:
    text = str(value or "")
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold_ics_line(line: str, limit: int = 75) -> str:
    """Fold a content line to at most ``limit`` characters (RFC 5545 style)."""
    if len(line) <= limit:
        return line
    parts = [line[:limit]]
    rest = line[limit:]
    while rest:
        parts.append(" " + rest[: limit - 1])
        rest = rest[limit - 1:]
    return "\r\n".join(parts)


def generate_interview_ics(
    *,
    candidate_name: str,
    candidate_email: str,
    position: str,
    company: str = "",
    start: datetime,
    duration_minutes: Optional[int] = None,
    location: str = "",
    description: str = "",
    organizer_email: str = "",
    organizer_name: str = "HR Team",
    uid: Optional[str] = None,
) -> str:
    """Build a deterministic, importable ``.ics`` invitation."""
    tz = config.get_interview_timezone()
    start_at = _ensure_aware(start, tz)
    duration = int(duration_minutes or config.INTERVIEW_SLOT_MINUTES)
    end_at = start_at + timedelta(minutes=max(15, duration))
    start_utc = start_at.astimezone(timezone.utc)
    end_utc = end_at.astimezone(timezone.utc)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    event_uid = uid or f"{uuid.uuid4().hex}@smart-hr-recruitment"
    summary = f"Interview - {position}" + (f" at {company}" if company else "")
    body = description or (
        f"Interview for the {position} role"
        + (f" at {company}" if company else "")
        + f" with {candidate_name or 'the candidate'}."
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Smart HR Recruitment//Interview Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(body)}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
    ]
    if location:
        lines.append(f"LOCATION:{_ics_escape(location)}")
    if organizer_email:
        lines.append(
            f"ORGANIZER;CN={_ics_escape(organizer_name)}:mailto:{_ics_escape(organizer_email)}"
        )
    if candidate_email:
        lines.append(
            "ATTENDEE;CN="
            f"{_ics_escape(candidate_name or candidate_email)};RSVP=TRUE:mailto:"
            f"{_ics_escape(candidate_email)}"
        )
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(_fold_ics_line(line) for line in lines) + "\r\n"


__all__ = [
    "BusyPeriod",
    "CalendarAPIError",
    "CalendarClient",
    "CalendarNoSlots",
    "generate_interview_ics",
]
