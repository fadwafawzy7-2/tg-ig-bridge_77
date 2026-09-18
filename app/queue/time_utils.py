"""
Small timezone helpers shared by the Phase 6 DB-orchestration modules.

Pure stdlib (`datetime` + `zoneinfo`) — zero third-party dependency, so
this stays directly testable like `eligibility.py`/`scoring.py`.
Deliberately does NOT import `app.core.config` itself: callers pass the
timezone name explicitly (typically `settings.SCHEDULER_TIMEZONE`), so
this module has no dependency on pydantic-settings either and can be
imported/tested on its own.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Timezone-aware current time in UTC."""
    return datetime.now(timezone.utc)


def now_in_timezone(tz_name: str) -> datetime:
    """Timezone-aware current time in the given IANA timezone (e.g.
    `"Asia/Gaza"`)."""
    return datetime.now(ZoneInfo(tz_name))


def today_in_timezone(tz_name: str) -> date:
    """The current calendar date as observed in the given timezone — not
    necessarily the same as `date.today()` (UTC-based), which is exactly
    why this exists: "today" for daily_limits purposes must follow the
    scheduler's configured timezone, not the server's."""
    return now_in_timezone(tz_name).date()


def start_of_day_in_timezone(day: date, tz_name: str) -> datetime:
    """Timezone-aware midnight at the start of `day` in `tz_name`."""
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tz_name))
