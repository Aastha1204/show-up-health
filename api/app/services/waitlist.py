"""Waitlist engine (spec 7.4): a slot frees up -> offer it to ONE waiting patient at a time, first come first served.

Triggers: Cancel, reschedule-away, a declined offer, an expired offer, a removed entry. Each offers the slot to the
next eligible candidate, as long as the slot still starts more than 60 minutes from now."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from psycopg.types.json import Jsonb

from .. import jobs
from ..messaging import guards, templates
from ..messaging.service import Outgoing
from ..reminders import fmt_date, fmt_time
from ..timeutil import to_ist, window_of
from . import appointments as svc
from .slots import is_slot_valid

log = logging.getLogger("showup.waitlist")

MIN_NOTICE = timedelta(minutes=60)
ACTIVE = ("pending", "confirmed", "rescheduled", "arrived")

UNAVAILABLE = "Sorry, that offer is no longer available. We will keep you on the waitlist for other openings."
EXPIRED = "Sorry, that offer has expired. We will keep you on the waitlist for other openings."
TAKEN = "Sorry, that slot was just taken. We will keep you on the waitlist for other openings."
DECLINED = "No problem. We will keep you on the waitlist for other openings."
CLASH = "Sorry, that time overlaps another appointment you have, so we could not book it."


class _NotSent(Exception):
    def __init__(self, reason):
        self.reason = reason


async def _one(conn, sql, args):
    return await (await conn.execute(sql, args)).fetchone()


async def _all(conn, sql, args):
    return await (await conn.execute(sql, args)).fetchall()


async def doctor_free(conn, doctor_id, start_at: datetime, duration_min: int) -> bool:
    end = start_at + timedelta(minutes=duration_min)
    return not await _one(conn, """select 1 as x from appointment where doctor_id=%s and status = any(%s)
                                   and start_at < %s and end_at > %s limit 1""",
                          [doctor_id, list(ACTIVE), end, start_at])


async def patient_busy(conn, patient_id, start_at: datetime, duration_min: int) -> bool:
    end = start_at + timedelta(minutes=duration_min)
    return bool(await _one(conn, """select 1 as x from appointment where patient_id=%s and status = any(%s)
                                    and start_at < %s and end_at > %s limit 1""",
                           [patient_id, list(ACTIVE), end, start_at]))


async def candidates(conn, clinic_id, doctor_id, start_at: datetime) -> list[dict]:
    """Waiting entries for that IST date, matching doctor (or any), whose window covers the slot time, patient not
    opted out, never already offered THIS slot; oldest first."""
    local = to_ist(start_at)
    return await _all(conn, """
        select e.*, t.duration_min as tt_duration
        from waitlist_entry e
        join patient p on p.id = e.patient_id
        left join treatment_type t on t.id = e.treatment_type_id
        where e.clinic_id=%s and e.status='waiting' and e.preferred_date=%s
          and (e.doctor_id is null or e.doctor_id=%s)
          and e.preferred_window in ('any', %s)
          and not p.opted_out
          and not exists (select 1 from waitlist_offer o where o.waitlist_entry_id=e.id
                          and o.doctor_id=%s and o.slot_start_at=%s)
        order by e.created_at, e.id""",
        [clinic_id, local.date(), doctor_id, window_of(local.time()), doctor_id, start_at])


def _offer_message(clinic, patient, doctor, offer_id, start_at, ttl_min) -> Outgoing:
    values = {"patient_name": jobs.first_name(patient["name"]), "clinic_name": clinic["name"],
              "date": fmt_date(start_at), "time": fmt_time(start_at), "doctor_name": doctor["name"], "ttl_min": ttl_min}
    r = templates.render("showup_waitlist_offer", patient["language"], values, ids={"offer_id": offer_id})
    return Outgoing.from_rendered(r)


async def offer_freed_slot(conn, messaging, clinic: dict, doctor_id: UUID, start_at: datetime,
                           duration_min: int, now: datetime) -> UUID | None:
    """Offer a just-freed slot to the next eligible candidate. Returns the offer id, or None if no offer was made
    (too close to start, quiet hours, someone already holds a pending offer, or nobody eligible)."""
    if start_at - now <= MIN_NOTICE:
        return None
    if guards.is_quiet(to_ist(now).time(), clinic["quiet_hours_start"], clinic["quiet_hours_end"]):
        log.info("waitlist offer for %s skipped: quiet hours (the slot shows as an open gap)", start_at)
        return None
    # One pending offer per freed slot: serialise on the slot, then look. (A unique index backs this up.)
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 1))", [f"{doctor_id}|{start_at.isoformat()}"])
    if await _one(conn, "select 1 as x from waitlist_offer where doctor_id=%s and slot_start_at=%s and status='pending'",
                  [doctor_id, start_at]):
        return None
    doctor = await _one(conn, "select * from doctor where id=%s and clinic_id=%s and active", [doctor_id, clinic["id"]])
    if not doctor:
        return None
    ttl = clinic["waitlist_offer_ttl_min"]
    for e in await candidates(conn, clinic["id"], doctor_id, start_at):
        dur = e["tt_duration"] or duration_min
        if not is_slot_valid(doctor["working_hours"], start_at, dur):
            continue
        if not await doctor_free(conn, doctor_id, start_at, dur) or await patient_busy(conn, e["patient_id"], start_at, dur):
            continue
        patient = await _one(conn, "select * from patient where id=%s", [e["patient_id"]])
        try:
            async with conn.transaction():
                offer = await _one(conn, """insert into waitlist_offer (clinic_id, waitlist_entry_id, doctor_id, slot_start_at,
                                            duration_min, status, expires_at) values (%s,%s,%s,%s,%s,'pending',%s) returning *""",
                                   [clinic["id"], e["id"], doctor_id, start_at, dur, now + timedelta(minutes=ttl)])
                res = await messaging.send(conn, clinic, _offer_message(clinic, patient, doctor, offer["id"], start_at, ttl),
                                           patient=patient, proactive=True, now=now)
                if res.outcome != "sent":
                    raise _NotSent(res.reason)
                await conn.execute("update waitlist_offer set outbound_message_id=%s where id=%s", [res.outbound_id, offer["id"]])
                await conn.execute("update waitlist_entry set status='offered' where id=%s", [e["id"]])
        except _NotSent as ns:
            log.info("offer to entry %s not sent (%s); trying the next candidate", e["id"], ns.reason)
            continue
        return offer["id"]
    return None


async def respond_to_offer(conn, messaging, clinic: dict, patient: dict, offer_id: UUID | None, accept: bool,
                           now: datetime) -> tuple[str, str | None]:
    """Patient tapped Book (accept) or Not now. Returns (outcome, reply text or None for silence). Idempotent."""
    offer = None
    if offer_id:
        offer = await _one(conn, """select o.*, e.patient_id, e.treatment_type_id from waitlist_offer o
                                    join waitlist_entry e on e.id = o.waitlist_entry_id
                                    where o.id=%s for update of o""", [offer_id])
    if not offer or offer["patient_id"] != patient["id"]:
        return "unavailable", UNAVAILABLE                      # unknown or someone else's offer: reveal nothing
    mine = "accepted" if accept else "declined"
    if offer["status"] == mine:
        return "already_" + mine, None                         # repeated tap: no-op
    if offer["status"] != "pending":
        return offer["status"], EXPIRED if offer["status"] == "expired" else (TAKEN if offer["status"] == "slot_taken" else UNAVAILABLE)

    async def release(status: str):
        await conn.execute("update waitlist_offer set status=%s, responded_at=%s where id=%s", [status, now, offer["id"]])
        await conn.execute("update waitlist_entry set status='waiting' where id=%s and status='offered'", [offer["waitlist_entry_id"]])

    async def pass_it_on():
        await offer_freed_slot(conn, messaging, clinic, offer["doctor_id"], offer["slot_start_at"], offer["duration_min"], now)

    if offer["expires_at"] <= now:                              # a Book tap after expiry is declined politely
        await release("expired")
        await pass_it_on()
        return "expired", EXPIRED
    if not accept:
        await release("declined")
        await pass_it_on()
        return "declined", DECLINED
    if await patient_busy(conn, patient["id"], offer["slot_start_at"], offer["duration_min"]):
        await release("declined")
        await pass_it_on()
        return "clash", CLASH
    tt_id = offer["treatment_type_id"] or (await _one(
        conn, "select id from treatment_type where clinic_id=%s order by duration_min, name limit 1", [clinic["id"]]))["id"]
    try:   # one transaction; the exclusion constraint is the final judge
        appt = await svc.create_appointment(
            conn, clinic_id=clinic["id"], patient_id=patient["id"], doctor_id=offer["doctor_id"], treatment_type_id=tt_id,
            start_at=offer["slot_start_at"], source="waitlist", actor="patient", status="confirmed",
            recovered_via_waitlist=True, now=now)
    except (svc.Conflict, svc.Invalid, svc.NotFound):
        await release("slot_taken")
        return "slot_taken", TAKEN
    await conn.execute("update waitlist_offer set status='accepted', responded_at=%s where id=%s", [now, offer["id"]])
    await conn.execute("update waitlist_entry set status='booked' where id=%s", [offer["waitlist_entry_id"]])
    job = await _one(conn, """select * from reminder_job where appointment_id=%s and type='confirmation' and status='scheduled'
                              order by created_at desc limit 1 for update""", [appt["id"]])
    if job:                                                     # the confirmation goes out now, through the normal path
        outcome = await jobs.execute_job(conn, messaging, job, now=now, force=True)
        await jobs._finish(conn, job, outcome, now)
    return "booked", None


async def expire_due_offers(pool, messaging, now: datetime, limit: int = 100) -> int:
    """Mark overdue pending offers expired and pass each slot to the next candidate. One offer per transaction,
    claimed with SKIP LOCKED, so concurrent ticks never double-offer."""
    n = 0
    while n < limit:
        async with pool.connection() as conn:
            o = await _one(conn, """select * from waitlist_offer where status='pending' and expires_at <= %s
                                    order by expires_at limit 1 for update skip locked""", [now])
            if not o:
                break
            await conn.execute("update waitlist_offer set status='expired', responded_at=%s where id=%s", [now, o["id"]])
            await conn.execute("update waitlist_entry set status='waiting' where id=%s and status='offered'",
                               [o["waitlist_entry_id"]])
            clinic = await _one(conn, "select * from clinic where id=%s", [o["clinic_id"]])
            await offer_freed_slot(conn, messaging, clinic, o["doctor_id"], o["slot_start_at"], o["duration_min"], now)
        n += 1
    return n


async def remove_entry(conn, messaging, clinic: dict, entry_id: UUID, now: datetime) -> bool:
    """Staff removes an entry. If it held a pending offer, that offer lapses and the slot moves to the next person."""
    entry = await _one(conn, "select * from waitlist_entry where id=%s and clinic_id=%s for update", [entry_id, clinic["id"]])
    if not entry or entry["status"] in ("removed", "booked"):
        return False
    offer = await _one(conn, "select * from waitlist_offer where waitlist_entry_id=%s and status='pending' for update", [entry_id])
    await conn.execute("update waitlist_entry set status='removed' where id=%s", [entry_id])
    if offer:
        await conn.execute("update waitlist_offer set status='expired', responded_at=%s where id=%s", [now, offer["id"]])
        await offer_freed_slot(conn, messaging, clinic, offer["doctor_id"], offer["slot_start_at"], offer["duration_min"], now)
    return True
