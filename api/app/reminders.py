"""Reminder job scheduling (spec 7.1). Jobs are re-checked at execution time (jobs.py); this only decides *when*."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from uuid import UUID

from .messaging.guards import shift_for_quiet_hours
from .timeutil import ist_to_utc, ist_today, parse_hhmm, to_ist, weekday_key

APPT_JOB_TYPES = ("confirmation", "day_before", "two_hour")
ALLOWED_LATE_DAY_BEFORE = timedelta(hours=3)


def fmt_date(dt: datetime) -> str:
    d = to_ist(dt)
    return f"{d:%a} {d.day} {d:%b}"


def fmt_time(dt: datetime) -> str:
    d = to_ist(dt)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def _setting(clinic: dict, typ: str, key: str, default):
    return ((clinic.get("reminder_settings") or {}).get(typ) or {}).get(key, default)


def enabled(clinic: dict, typ: str) -> bool:
    return bool(_setting(clinic, typ, "enabled", True))


async def _insert_job(conn, clinic, typ, due, *, appt=None, patient_id=None, doctor_id=None) -> dict:
    row = await (await conn.execute(
        """insert into reminder_job (clinic_id, appointment_id, patient_id, doctor_id, type, due_at)
           values (%s,%s,%s,%s,%s,%s) returning *""",
        [clinic["id"], appt["id"] if appt else None, patient_id or (appt["patient_id"] if appt else None),
         doctor_id or (appt["doctor_id"] if appt else None), typ, due])).fetchone()
    return row


async def cancel_scheduled(conn, appt_id: UUID, types: tuple[str, ...] | None = None) -> int:
    q = "update reminder_job set status='cancelled' where appointment_id=%s and status='scheduled'"
    args: list = [appt_id]
    if types:
        q += " and type = any(%s)"
        args.append(list(types))
    cur = await conn.execute(q, args)
    return cur.rowcount


async def schedule_for_appointment(conn, appt: dict, clinic: dict, now: datetime, *, include_confirmation: bool = True) -> list[dict]:
    """(Re)generate confirmation / day_before / two_hour for an appointment. Cancels the old scheduled ones first."""
    await cancel_scheduled(conn, appt["id"], APPT_JOB_TYPES)
    if appt["start_at"] <= now or appt["status"] not in ("pending", "confirmed", "rescheduled"):
        return []
    qs, qe = clinic["quiet_hours_start"], clinic["quiet_hours_end"]
    shift = lambda due: shift_for_quiet_hours(due, now, qs, qe)  # noqa: E731
    start, jobs = appt["start_at"], []

    conf_due = shift(now)                                           # "immediately", unless it is night
    if include_confirmation and enabled(clinic, "confirmation"):
        jobs.append(await _insert_job(conn, clinic, "confirmation", conf_due, appt=appt))

    if enabled(clinic, "day_before") and appt["status"] != "confirmed":
        due = start - timedelta(minutes=_setting(clinic, "day_before", "offset_min", 1440))
        if due < now:  # booked inside the 24h window
            due = shift(conf_due + timedelta(minutes=5)) if start - now > ALLOWED_LATE_DAY_BEFORE else None
        else:
            due = shift(due)
        if due:
            jobs.append(await _insert_job(conn, clinic, "day_before", due, appt=appt))

    if enabled(clinic, "two_hour"):
        due = start - timedelta(minutes=_setting(clinic, "two_hour", "offset_min", 120))
        if due >= now:
            jobs.append(await _insert_job(conn, clinic, "two_hour", shift(due), appt=appt))
    return jobs


async def schedule_post_visit(conn, appt: dict, clinic: dict, now: datetime) -> dict | None:
    if not enabled(clinic, "post_visit"):
        return None
    exists = await (await conn.execute(
        "select 1 as x from reminder_job where appointment_id=%s and type='post_visit' and status in ('scheduled','sent')",
        [appt["id"]])).fetchone()
    if exists:
        return None
    due = appt["end_at"] + timedelta(minutes=_setting(clinic, "post_visit", "offset_min", 30))
    due = shift_for_quiet_hours(max(due, now), now, clinic["quiet_hours_start"], clinic["quiet_hours_end"])
    return await _insert_job(conn, clinic, "post_visit", due, appt=appt)


async def schedule_recall(conn, appt: dict, clinic: dict, sent_at: datetime) -> dict | None:
    """After the post-visit message: recall_after_days later, at 10:30 IST."""
    if not enabled(clinic, "recall") or not appt.get("treatment_type_id"):
        return None
    tt = await (await conn.execute("select recall_after_days from treatment_type where id=%s",
                                   [appt["treatment_type_id"]])).fetchone()
    if not tt or not tt["recall_after_days"]:
        return None
    due = ist_to_utc(to_ist(sent_at).date() + timedelta(days=tt["recall_after_days"]), time(10, 30))
    return await _insert_job(conn, clinic, "recall", due, appt=appt)


async def ensure_daily_summaries(conn, clinic: dict, now: datetime) -> int:
    """One 08:00 IST summary per active doctor, for today (if within a 30-min grace) and tomorrow. Idempotent."""
    created = 0
    today = ist_today(now)
    docs = await (await conn.execute("select * from doctor where clinic_id=%s and active", [clinic["id"]])).fetchall()
    for doc in docs:
        for d in (today, today + timedelta(days=1)):
            if not (doc["working_hours"] or {}).get(weekday_key(d)):
                continue
            due = ist_to_utc(d, parse_hhmm(_setting(clinic, "daily_summary", "at", "08:00")))
            if due < now - timedelta(minutes=30) or not enabled(clinic, "daily_summary"):
                continue
            cur = await conn.execute(
                """insert into reminder_job (clinic_id, doctor_id, type, due_at) values (%s,%s,'daily_summary',%s)
                   on conflict (doctor_id, due_at) where type = 'daily_summary' do nothing""",
                [clinic["id"], doc["id"], due])
            created += cur.rowcount
    return created
