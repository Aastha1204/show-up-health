"""Slot generation. `compute_slots` is pure (no DB) so it is unit-testable; `fetch_available_slots` wires it up."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Sequence
from uuid import UUID

from ..timeutil import IST, UTC, ist_day_bounds, ist_to_utc, parse_hhmm, weekday_key

GRID_MIN = 15
MIN_LEAD = timedelta(0)

Interval = tuple[datetime, datetime]


def working_ranges(working_hours: dict, d: date) -> list[tuple[time, time]]:
    return [(parse_hhmm(a), parse_hhmm(b)) for a, b in (working_hours or {}).get(weekday_key(d), [])]


def overlaps(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    """Half-open [a0,a1) vs [b0,b1) — touching intervals do not overlap (matches the DB constraint)."""
    return a0 < b1 and b0 < a1


def compute_slots(
    working_hours: dict,
    d: date,
    duration_min: int,
    busy: Iterable[Interval],
    now: datetime,
    *,
    min_lead: timedelta = MIN_LEAD,
    step_min: int = GRID_MIN,
) -> list[datetime]:
    """Start times (aware UTC) on IST date `d` where `duration_min` fits inside working hours,
    clear of every `busy` interval, and not earlier than now + min_lead."""
    busy = sorted(busy)
    out: list[datetime] = []
    earliest = now + min_lead
    dur = timedelta(minutes=duration_min)
    for r_start, r_end in working_ranges(working_hours, d):
        cur = ist_to_utc(d, r_start)
        end = ist_to_utc(d, r_end)
        while cur + dur <= end:
            if cur >= earliest and not any(overlaps(cur, cur + dur, b0, b1) for b0, b1 in busy):
                out.append(cur)
            cur += timedelta(minutes=step_min)
    return out


def is_slot_valid(working_hours: dict, start_at: datetime, duration_min: int) -> bool:
    """Is [start, start+duration) wholly inside one working range and on the 15-min IST grid?"""
    local = start_at.astimezone(IST)
    if local.minute % GRID_MIN or local.second or local.microsecond:
        return False
    end = start_at + timedelta(minutes=duration_min)
    d = local.date()
    for r_start, r_end in working_ranges(working_hours, d):
        if ist_to_utc(d, r_start) <= start_at and end <= ist_to_utc(d, r_end):
            return True
    return False


async def fetch_busy(conn, doctor_id: UUID, d: date, exclude_appointment_id: UUID | None = None) -> list[Interval]:
    lo, hi = ist_day_bounds(d)
    q = """select start_at, end_at from appointment
           where doctor_id = %s and start_at < %s and end_at > %s
             and status in ('pending','confirmed','rescheduled','arrived')"""
    args: list = [doctor_id, hi, lo]
    if exclude_appointment_id:
        q += " and id <> %s"
        args.append(exclude_appointment_id)
    cur = await conn.execute(q, args)
    return [(r["start_at"], r["end_at"]) for r in await cur.fetchall()]


async def fetch_available_slots(
    conn, doctor: dict, d: date, duration_min: int, now: datetime,
    exclude_appointment_id: UUID | None = None,
) -> list[datetime]:
    busy = await fetch_busy(conn, doctor["id"], d, exclude_appointment_id)
    return compute_slots(doctor["working_hours"], d, duration_min, busy, now)
