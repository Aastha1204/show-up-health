"""Executing reminder jobs and the scheduler tick. Everything is re-checked at execution time (spec 7.1/7.2)."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from . import reminders
from .messaging import templates
from .messaging.service import MessagingService, Outgoing
from .reminders import fmt_date, fmt_time
from .services.slots import fetch_busy, working_ranges
from .timeutil import IST, ist_day_bounds, ist_to_utc, ist_today, to_ist, utcnow

log = logging.getLogger("showup.jobs")
MAX_ATTEMPTS = 3
ACTIVE_UPCOMING = ("pending", "confirmed", "rescheduled")


@dataclass
class JobOutcome:
    status: str                       # sent | skipped | failed | retry
    reason: str | None = None
    outbound_id: UUID | None = None


async def _one(conn, sql, args):
    return await (await conn.execute(sql, args)).fetchone()


def first_name(name: str) -> str:
    return templates.one_line(name).split(" ")[0]


def _loc(clinic) -> str:
    return templates.one_line(clinic.get("maps_link") or clinic.get("address") or clinic["name"])


async def open_slot_starts(conn, doctor: dict, d, now: datetime, limit: int = 6) -> list[str]:
    """Start of each free gap (>= 20 min, still in the future) inside working hours on IST date d."""
    busy = await fetch_busy(conn, doctor["id"], d)
    starts = []
    for r0, r1 in working_ranges(doctor["working_hours"], d):
        cur, end = ist_to_utc(d, r0), ist_to_utc(d, r1)
        for b0, b1 in busy:
            if b1 <= cur or b0 >= end:
                continue
            if b0 - cur >= timedelta(minutes=20) and cur >= now:
                starts.append(cur)
            cur = max(cur, b1)
        if end - cur >= timedelta(minutes=20) and cur >= now:
            starts.append(cur)
    return [f"{to_ist(s):%H:%M}" for s in starts][:limit]


async def summary_values(conn, clinic: dict, doctor: dict, now: datetime, day=None) -> dict:
    """total = not cancelled; confirmed = confirmed/rescheduled/arrived; unconfirmed = still pending."""
    d = day or ist_today(now)
    lo, hi = ist_day_bounds(d)
    rows = await (await conn.execute(
        """select status, count(*) as n from appointment where doctor_id=%s and start_at >= %s and start_at < %s
           group by status""", [doctor["id"], lo, hi])).fetchall()
    c = {r["status"]: r["n"] for r in rows}
    confirmed = sum(c.get(s, 0) for s in ("confirmed", "rescheduled", "arrived"))
    unconf = c.get("pending", 0)
    slots = await open_slot_starts(conn, doctor, d, now)
    return {"doctor_name": doctor["name"], "date": fmt_date(lo), "total": confirmed + unconf + c.get("no_show", 0),
            "confirmed": confirmed, "unconfirmed": unconf, "open_slots": ", ".join(slots) if slots else "none"}


def appointment_values(clinic: dict, patient: dict, doctor: dict, appt: dict) -> dict:
    """Variable values shared by the appointment templates. Never includes the treatment."""
    return {"patient_name": first_name(patient["name"]), "clinic_name": clinic["name"],
            "date": fmt_date(appt["start_at"]), "time": fmt_time(appt["start_at"]), "doctor_name": doctor["name"],
            "location": _loc(clinic), "directions": _loc(clinic),
            "parking": templates.one_line(clinic.get("parking_note") or "Parking is available nearby"),
            "review_link": clinic.get("review_link") or ""}


def appointment_message(name: str, clinic, patient, doctor, appt, extra: dict | None = None) -> Outgoing:
    values = {**appointment_values(clinic, patient, doctor, appt), **(extra or {})}
    r = templates.render(name, patient["language"], values,
                         ids={"appointment_id": appt["id"], "patient_id": patient["id"]})
    return Outgoing.from_rendered(r, appointment_id=appt["id"])


TEMPLATE_FOR_JOB = {"confirmation": "showup_booking_confirmation", "day_before": "showup_day_before",
                    "two_hour": "showup_two_hour", "post_visit": "showup_post_visit", "recall": "showup_recall"}


async def execute_job(conn, messaging: MessagingService, job: dict, *, now: datetime, force: bool = False) -> JobOutcome:
    """Re-check the world, build the message, send it. `force` (demo 'Fire now') skips timing/quiet-hour checks only;
    opt-out, seed routing and the medical-content guard always apply."""
    clinic = await _one(conn, "select * from clinic where id=%s", [job["clinic_id"]])
    typ = job["type"]

    if typ == "daily_summary":
        doctor = await _one(conn, "select * from doctor where id=%s", [job["doctor_id"]])
        if not doctor or not doctor["active"]:
            return JobOutcome("skipped", "doctor_inactive")
        v = await summary_values(conn, clinic, doctor, now)
        r = templates.render("showup_daily_summary", "en", v)
        out = Outgoing.from_rendered(r)
        res = await messaging.send(conn, clinic, out, doctor=doctor, proactive=True, ignore_quiet_hours=force, now=now)
        return _map(res)

    patient = await _one(conn, "select * from patient where id=%s", [job["patient_id"]])
    appt = await _one(conn, "select * from appointment where id=%s", [job["appointment_id"]]) \
        if job["appointment_id"] else None
    if patient["opted_out"]:
        return JobOutcome("skipped", "opted_out")

    if typ in ("confirmation", "day_before", "two_hour"):
        if not appt or appt["status"] not in ACTIVE_UPCOMING:
            return JobOutcome("skipped", "appointment_inactive")
        if appt["start_at"] <= now:
            return JobOutcome("skipped", "in_the_past")
        if typ == "day_before" and appt["status"] == "confirmed" and not force:
            return JobOutcome("skipped", "already_confirmed")
    elif typ == "post_visit":
        if not appt or appt["status"] == "cancelled" or (appt["status"] != "arrived" and not force):
            return JobOutcome("skipped", "appointment_inactive")
        if not (clinic.get("review_link") or "").strip():
            return JobOutcome("skipped", "no_review_link")  # the template ends with the link; never send it blank
    elif typ == "recall":
        upcoming = await _one(conn, "select 1 as x from appointment where patient_id=%s and start_at > %s and status = any(%s)",
                              [patient["id"], now, list(ACTIVE_UPCOMING)])
        if upcoming and not force:
            return JobOutcome("skipped", "has_upcoming_appointment")

    doctor = await _one(conn, "select * from doctor where id=%s", [appt["doctor_id"] if appt else job["doctor_id"]]) \
        if (appt or job["doctor_id"]) else None
    extra = None
    if typ == "recall":
        last = appt["start_at"] if appt else now
        extra = {"months": max(1, round((now - last).days / 30))}
    if appt is None:  # recall without an appointment row: synthesise just enough for the values
        appt = {"id": None, "start_at": now}
        doctor = doctor or {"name": clinic["name"]}
    out = appointment_message(TEMPLATE_FOR_JOB[typ], clinic, patient, doctor, appt, extra)
    res = await messaging.send(conn, clinic, out, patient=patient, proactive=True, ignore_quiet_hours=force, now=now)
    outcome = _map(res)
    if outcome.status == "sent" and typ == "post_visit":
        await reminders.schedule_recall(conn, appt, clinic, now)
    return outcome


def _map(res) -> JobOutcome:
    if res.outcome == "sent":
        return JobOutcome("sent", None, res.outbound_id)
    if res.outcome == "skipped":
        return JobOutcome("skipped", res.reason)
    if res.outcome == "blocked":
        return JobOutcome("failed", res.reason)            # medical_content: never retried
    return JobOutcome("retry" if res.transient else "failed", res.reason, res.outbound_id)


async def _finish(conn, job: dict, o: JobOutcome, now: datetime) -> None:
    attempts = job["attempts"] + (1 if o.status in ("retry", "failed") else 0)
    if o.status == "retry" and attempts < MAX_ATTEMPTS:
        await conn.execute("update reminder_job set attempts=%s, due_at=%s, skip_reason=%s where id=%s",
                           [attempts, now + timedelta(minutes=attempts), o.reason, job["id"]])
        return
    status = "failed" if o.status in ("failed", "retry") else o.status
    await conn.execute(
        """update reminder_job set status=%s, skip_reason=%s, sent_at=%s, outbound_message_id=%s, attempts=%s
           where id=%s""",
        [status, o.reason, now if status == "sent" else None, o.outbound_id, attempts, job["id"]])


async def run_one_due(pool, messaging: MessagingService, now: datetime) -> JobOutcome | None:
    """Claim ONE due job with FOR UPDATE SKIP LOCKED, run it, mark it, commit. Concurrent ticks never share a job."""
    async with pool.connection() as conn:
        job = await _one(conn, """select * from reminder_job where status='scheduled' and due_at <= %s
                                  order by due_at limit 1 for update skip locked""", [now])
        if not job:
            return None
        try:
            async with conn.transaction():  # savepoint: a crash inside leaves the claim row updatable
                outcome = await execute_job(conn, messaging, job, now=now)
        except Exception as e:
            log.exception("job %s (%s) crashed", job["id"], job["type"])
            outcome = JobOutcome("failed", f"exception: {type(e).__name__}")
        if outcome.status == "failed" and outcome.reason == "medical_content":
            log.error("JOB FAILED medical_content job=%s type=%s", job["id"], job["type"])
        await _finish(conn, job, outcome, now)
        return outcome


async def tick(pool, messaging: MessagingService, now: datetime | None = None, max_jobs: int = 200) -> Counter:
    now = now or utcnow()
    counts: Counter = Counter()
    async with pool.connection() as conn:
        for clinic in await (await conn.execute("select * from clinic")).fetchall():
            counts["summaries_created"] += await reminders.ensure_daily_summaries(conn, clinic, now)
    from .services import waitlist   # late import: waitlist uses this module to run confirmation jobs
    counts["offers_expired"] += await waitlist.expire_due_offers(pool, messaging, now)
    for _ in range(max_jobs):
        o = await run_one_due(pool, messaging, now)
        if o is None:
            break
        counts[o.status] += 1
    return counts
