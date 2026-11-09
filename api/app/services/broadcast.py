"""Running-late broadcast (spec 7.5). Recipients: that doctor's remaining appointments today (IST) that are pending,
confirmed or rescheduled, start after now, and whose patient has not opted out. One message per patient."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from uuid import UUID

from .. import jobs
from ..messaging import templates
from ..messaging.service import Outgoing
from ..reminders import fmt_time
from ..timeutil import ist_day_bounds, ist_today
from .appointments import Conflict, Invalid, NotFound

log = logging.getLogger("showup.broadcast")
DELAYS = (15, 30, 45, 60)
DUPLICATE_WINDOW = timedelta(seconds=60)


async def _all(conn, sql, args):
    return await (await conn.execute(sql, args)).fetchall()


async def recipients(conn, clinic_id: UUID, doctor_id: UUID, now: datetime) -> list[dict]:
    """Unique patients, each with their EARLIEST remaining appointment today, soonest first."""
    _, day_end = ist_day_bounds(ist_today(now))
    rows = await _all(conn, """
        select distinct on (p.id) a.id as appointment_id, a.start_at, p.id as patient_id, p.name, p.language
        from appointment a join patient p on p.id = a.patient_id
        where a.clinic_id=%s and a.doctor_id=%s and a.status in ('pending','confirmed','rescheduled')
          and a.start_at > %s and a.start_at < %s and not p.opted_out
        order by p.id, a.start_at""", [clinic_id, doctor_id, now, day_end])
    return sorted(rows, key=lambda r: r["start_at"])


def _check(delay_min: int) -> None:
    if delay_min not in DELAYS:
        raise Invalid(f"Choose a delay of {', '.join(map(str, DELAYS))} minutes", "bad_delay")


def _render(patient_name: str, doctor_name: str, delay_min: int, when, language: str = "en"):
    return templates.render("showup_running_late", language, {
        "patient_name": jobs.first_name(patient_name), "doctor_name": doctor_name,
        "delay_min": delay_min, "time": fmt_time(when)})


async def preview(conn, clinic_id: UUID, doctor_id: UUID, delay_min: int, now: datetime) -> dict:
    _check(delay_min)
    doc = (await _all(conn, "select * from doctor where id=%s and clinic_id=%s and active", [doctor_id, clinic_id]))
    if not doc:
        raise NotFound("Doctor not found", "doctor_not_found")
    rec = await recipients(conn, clinic_id, doctor_id, now)
    first = rec[0] if rec else None
    sample = _render(first["name"] if first else "Priya", doc[0]["name"], delay_min,
                     first["start_at"] if first else now)
    return {"doctor_id": doctor_id, "doctor_name": doc[0]["name"], "delay_min": delay_min,
            "recipient_count": len(rec), "preview": sample.body,
            "recipients": [{"patient_id": r["patient_id"], "name": r["name"], "start_at": r["start_at"]} for r in rec]}


async def send_running_late(pool, messaging, *, clinic_id: UUID, doctor_id: UUID, delay_min: int,
                            created_by: UUID | None, now: datetime) -> dict:
    """Sends one message per recipient, each in its own transaction (a crash mid-way never re-sends the earlier ones
    on retry of the remainder, and never loses their records). A repeat within 60 s is refused as a double-click."""
    _check(delay_min)
    async with pool.connection() as conn:
        clinic = (await _all(conn, "select * from clinic where id=%s", [clinic_id]))[0]
        doctor = (await _all(conn, "select * from doctor where id=%s and clinic_id=%s and active", [doctor_id, clinic_id]))
        if not doctor:
            raise NotFound("Doctor not found", "doctor_not_found")
        doctor = doctor[0]
        dup = await _all(conn, """select 1 as x from broadcast where clinic_id=%s and doctor_id=%s and delay_min=%s
                                  and created_at > now() - make_interval(secs => %s) limit 1""",
                         [clinic_id, doctor_id, delay_min, DUPLICATE_WINDOW.total_seconds()])
        if dup:
            raise Conflict("That running-late message was just sent", "duplicate_broadcast")
        rec = await recipients(conn, clinic_id, doctor_id, now)
        if not rec:
            return {"broadcast_id": None, "targeted": 0, "sent": 0, "skipped": {}, "failed": 0}
        b = (await _all(conn, """insert into broadcast (clinic_id, doctor_id, delay_min, recipient_count, created_by)
                                 values (%s,%s,%s,%s,%s) returning id""",
                        [clinic_id, doctor_id, delay_min, len(rec), created_by]))[0]
    sent, failed, skipped = 0, 0, Counter()
    for r in rec:
        try:
            async with pool.connection() as conn:
                patient = (await _all(conn, "select * from patient where id=%s", [r["patient_id"]]))[0]
                rendered = _render(patient["name"], doctor["name"], delay_min, r["start_at"], patient["language"])
                out = Outgoing.from_rendered(rendered, appointment_id=r["appointment_id"])
                res = await messaging.send(conn, clinic, out, patient=patient, proactive=True, now=now)
        except Exception:
            log.exception("running-late send failed for patient %s", r["patient_id"])
            failed += 1
            continue
        if res.outcome == "sent":
            sent += 1
        elif res.outcome == "failed":
            failed += 1
        else:
            skipped[res.reason] += 1
    return {"broadcast_id": b["id"], "targeted": len(rec), "sent": sent, "skipped": dict(skipped), "failed": failed}
