"""All timestamps are stored as UTC timestamptz. Reasoning and display happen in Asia/Kolkata."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime: refusing to guess a timezone")
    return dt.astimezone(IST)


def ist_to_utc(d: date, t: time) -> datetime:
    """Combine an IST wall-clock date+time into an aware UTC datetime."""
    return datetime.combine(d, t, tzinfo=IST).astimezone(UTC)


def ist_day_bounds(d: date) -> tuple[datetime, datetime]:
    """[start, end) of an IST calendar day, as UTC instants."""
    lo = datetime.combine(d, time(0, 0), tzinfo=IST).astimezone(UTC)
    return lo, lo + timedelta(days=1)  # India has no DST, so a day is always 24h


def ist_today(now: datetime | None = None) -> date:
    return to_ist(now or utcnow()).date()


def weekday_key(d: date) -> str:
    return WEEKDAY_KEYS[d.weekday()]


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def day_before_due(start_at: datetime) -> datetime:
    """Raw due time of the 24h reminder (quiet-hours shifting is applied by the scheduler, Phase 2)."""
    return start_at - timedelta(hours=24)


def window_of(local_time: time) -> str:
    """Waitlist preference window for an IST wall-clock time."""
    if local_time < time(14, 0):
        return "morning"
    if local_time < time(17, 0):
        return "afternoon"
    return "evening"
