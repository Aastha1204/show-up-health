"""Reschedule: find alternative slots, render them as a WhatsApp interactive list, and parse the row the patient picks.

List limits (WhatsApp): at most 10 rows in total (9 slots + "None of these work"), row title <= 24 chars,
row description <= 72, button label <= 20, row id <= 200."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from ..reminders import fmt_date, fmt_time
from ..timeutil import ist_today, to_ist
from .slots import compute_slots, fetch_busy

MAX_SLOTS = 9
PER_DAY = 3
HORIZON = timedelta(days=7)
MIN_LEAD = timedelta(hours=2)
HEADER, BUTTON = "Pick a new time", "See slots"
NONE_TITLE = "None of these work"
BODY = "Choose a new time for your appointment."


@dataclass
class SlotOption:
    doctor_id: UUID
    doctor_name: str
    start_at: datetime


def spread(items: list, n: int) -> list:
    """Pick up to n items spread across the list (earliest, middle, latest...) so a day shows morning AND evening."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[0]]
    idx = sorted({round(i * (len(items) - 1) / (n - 1)) for i in range(n)})
    return [items[i] for i in idx]


async def find_slots(conn, appt: dict, now: datetime, *, max_total: int = MAX_SLOTS, per_day: int = PER_DAY) -> list[SlotOption]:
    """Same doctor and duration first; other doctors only if that doctor has fewer than max_total in the horizon.
    Inside working hours, at least 2 h from now, within 7 days, at most per_day per day (so the list spans days)."""
    docs = await (await conn.execute("select * from doctor where clinic_id=%s and active order by name",
                                     [appt["clinic_id"]])).fetchall()
    docs.sort(key=lambda d: d["id"] != appt["doctor_id"])           # the patient's own doctor first
    today, horizon_end = ist_today(now), now + HORIZON
    days = [today + timedelta(days=i) for i in range(0, 9)]

    async def day_slots(doc, d) -> list[datetime]:
        own = doc["id"] == appt["doctor_id"]
        busy = await fetch_busy(conn, doc["id"], d, appt["id"] if own else None)   # own slot may overlap a nearby move
        got = compute_slots(doc["working_hours"], d, appt["duration_min"], busy, now, min_lead=MIN_LEAD)
        return [s for s in got if s < horizon_end and not (own and s == appt["start_at"])]

    chosen: dict = {}
    total = 0
    for doc in docs:                                                # pass 1 = own doctor, pass 2.. = fill from others
        if total >= max_total:
            break
        for d in days:
            if total >= max_total:
                break
            have = chosen.setdefault(d, [])
            room = min(per_day - len(have), max_total - total)
            if room <= 0:
                continue
            for s in spread(await day_slots(doc, d), room):
                have.append(SlotOption(doc["id"], doc["name"], s))
                total += 1
    out = [o for d in sorted(chosen) for o in sorted(chosen[d], key=lambda o: o.start_at)]
    return out[:max_total]


def row_id(appt_id: UUID, doctor_id: UUID, start: datetime) -> str:
    return f"RS:{appt_id}:{doctor_id}:{int(start.timestamp())}"


def parse_row_id(s: str) -> tuple[UUID, UUID, datetime] | None:
    try:
        tag, a, d, e = s.split(":")
        if tag != "RS":
            return None
        return UUID(a), UUID(d), datetime.fromtimestamp(int(e), tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def build_list(appt_id: UUID, options: list[SlotOption]) -> dict:
    sections: dict[str, list[dict]] = {}
    for o in options:
        sections.setdefault(fmt_date(o.start_at), []).append({
            "id": row_id(appt_id, o.doctor_id, o.start_at),
            "title": f"{fmt_date(o.start_at)} · {fmt_time(o.start_at)}",   # e.g. "Tue 10 Nov · 10:30 AM" (21 chars)
            "description": o.doctor_name[:72],
        })
    secs = [{"title": t, "rows": rows} for t, rows in sections.items()]
    secs.append({"title": "Other", "rows": [{"id": f"RS_NONE:{appt_id}", "title": NONE_TITLE, "description": ""}]})
    return {"header": HEADER, "button": BUTTON, "sections": secs}
