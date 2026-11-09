from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg.errors
from psycopg.types.json import Jsonb

from .. import reminders
from ..timeutil import utcnow
from .slots import is_slot_valid

ACTIVE = ("pending", "confirmed", "rescheduled", "arrived")
# Terminal states have no outgoing edges. "rescheduled" is entered via move_appointment, not set_status.
TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "cancelled", "arrived", "no_show"},
    "confirmed": {"cancelled", "arrived", "no_show"},
    "rescheduled": {"confirmed", "cancelled", "arrived", "no_show"},
    "arrived": set(),
    "cancelled": set(),
    "no_show": set(),
}

OUTSIDE_HOURS_MSG = "That time is outside the doctor's working hours or not on the 15-minute grid"
CONFLICT_MSG = "That time overlaps another appointment for this doctor"


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.message, self.code = message, code


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    status_code = 409


class Invalid(DomainError):
    status_code = 422


async def _one(conn, sql: str, args: list):
    cur = await conn.execute(sql, args)
    return await cur.fetchone()


async def lock_doctors(conn, *doctor_ids: UUID) -> None:
    """Serialise writers per doctor (held until the surrounding transaction ends). Without this, two concurrent
    overlapping inserts can deadlock on the exclusion constraint instead of one cleanly losing. Sorted order
    avoids lock-order deadlocks when a move touches two doctors. The constraint remains the final judge."""
    for did in sorted({str(d) for d in doctor_ids}):
        await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", [did])


async def get_doctor(conn, clinic_id: UUID, doctor_id: UUID) -> dict:
    row = await _one(conn, "select * from doctor where id=%s and clinic_id=%s and active", [doctor_id, clinic_id])
    if not row:
        raise NotFound("Doctor not found", "doctor_not_found")
    return row


async def get_treatment(conn, clinic_id: UUID, tid: UUID) -> dict:
    row = await _one(conn, "select * from treatment_type where id=%s and clinic_id=%s", [tid, clinic_id])
    if not row:
        raise NotFound("Treatment type not found", "treatment_not_found")
    return row


async def get_or_create_patient(conn, clinic_id: UUID, data: dict, *, is_demo_seed: bool = False) -> dict:
    """Phone is unique per clinic: an existing patient is reused and their consent record left untouched."""
    return await _one(
        conn,
        """insert into patient (clinic_id, name, phone, language, consent_source, is_demo_seed)
           values (%s,%s,%s,%s,%s,%s)
           on conflict (clinic_id, phone) do update set updated_at = patient.updated_at
           returning *""",
        [clinic_id, data["name"], data["phone"], data["language"], data["consent_source"], is_demo_seed],
    )


async def log_event(conn, clinic_id, appt_id, from_status, to_status, actor, detail=None):
    await conn.execute(
        """insert into appointment_event (clinic_id, appointment_id, from_status, to_status, actor, detail)
           values (%s,%s,%s,%s,%s,%s)""",
        [clinic_id, appt_id, from_status, to_status, actor, Jsonb(detail or {})],
    )


async def create_appointment(
    conn, *, clinic_id: UUID, patient_id: UUID, doctor_id: UUID, treatment_type_id: UUID,
    start_at: datetime, source: str = "dashboard", internal_note: str | None = None,
    actor: str = "reception", status: str = "pending", enforce_hours: bool = True,
    recovered_via_waitlist: bool = False, now: datetime | None = None, schedule_reminders: bool = True,
    send_confirmation: bool = True,
) -> dict:
    now = now or utcnow()
    doctor = await get_doctor(conn, clinic_id, doctor_id)
    tt = await get_treatment(conn, clinic_id, treatment_type_id)
    if not await _one(conn, "select 1 as x from patient where id=%s and clinic_id=%s", [patient_id, clinic_id]):
        raise NotFound("Patient not found", "patient_not_found")
    if enforce_hours and not is_slot_valid(doctor["working_hours"], start_at, tt["duration_min"]):
        raise Invalid(OUTSIDE_HOURS_MSG, "outside_hours")
    try:
        async with conn.transaction():
            await lock_doctors(conn, doctor_id)
            row = await _one(
                conn,
                """insert into appointment (clinic_id, patient_id, doctor_id, treatment_type_id, start_at,
                       duration_min, status, source, avg_ticket_value, recovered_via_waitlist, internal_note)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
                [clinic_id, patient_id, doctor_id, treatment_type_id, start_at, tt["duration_min"],
                 status, source, tt["avg_ticket_value"], recovered_via_waitlist, internal_note],
            )
            await log_event(conn, clinic_id, row["id"], None, status, actor, {"created": True, "source": source})
            if schedule_reminders:
                clinic = await _one(conn, "select * from clinic where id=%s", [clinic_id])
                await reminders.schedule_for_appointment(conn, row, clinic, now, include_confirmation=send_confirmation)
    except (psycopg.errors.ExclusionViolation, psycopg.errors.DeadlockDetected):
        raise Conflict(CONFLICT_MSG, "slot_conflict")
    return row


async def get_appointment(conn, clinic_id: UUID, appt_id: UUID, *, for_update: bool = False) -> dict:
    row = await _one(
        conn, "select * from appointment where id=%s and clinic_id=%s" + (" for update" if for_update else ""),
        [appt_id, clinic_id],
    )
    if not row:
        raise NotFound("Appointment not found", "appointment_not_found")
    return row


async def move_appointment(
    conn, *, clinic_id: UUID, appt_id: UUID, start_at: datetime | None = None,
    doctor_id: UUID | None = None, treatment_type_id: UUID | None = None,
    internal_note: str | None = None, actor: str = "reception", enforce_hours: bool = True,
    now: datetime | None = None,
) -> dict:
    """Edit time/doctor/treatment. A change of time or doctor marks the appointment 'rescheduled'.
    The exclusion constraint is the final judge of conflicts."""
    async with conn.transaction():
        peek = await get_appointment(conn, clinic_id, appt_id)
        await lock_doctors(conn, peek["doctor_id"], doctor_id or peek["doctor_id"])
        cur = await get_appointment(conn, clinic_id, appt_id, for_update=True)
        if cur["status"] not in ("pending", "confirmed", "rescheduled"):
            raise Invalid("Only upcoming appointments can be edited", "not_editable")
        new_doc_id = doctor_id or cur["doctor_id"]
        new_tt_id = treatment_type_id or cur["treatment_type_id"]
        new_start = start_at or cur["start_at"]
        doctor = await get_doctor(conn, clinic_id, new_doc_id)
        tt = await get_treatment(conn, clinic_id, new_tt_id)
        moved = new_start != cur["start_at"] or new_doc_id != cur["doctor_id"]
        changed_shape = moved or tt["duration_min"] != cur["duration_min"]
        if changed_shape and enforce_hours and not is_slot_valid(doctor["working_hours"], new_start, tt["duration_min"]):
            raise Invalid(OUTSIDE_HOURS_MSG, "outside_hours")
        new_status = "rescheduled" if moved else cur["status"]
        try:
            async with conn.transaction():  # savepoint: a conflict leaves the outer transaction usable
                row = await _one(
                    conn,
                    """update appointment set doctor_id=%s, treatment_type_id=%s, start_at=%s, duration_min=%s,
                           avg_ticket_value=%s, status=%s, internal_note=coalesce(%s, internal_note)
                       where id=%s returning *""",
                    [new_doc_id, new_tt_id, new_start, tt["duration_min"], tt["avg_ticket_value"],
                     new_status, internal_note, appt_id],
                )
        except (psycopg.errors.ExclusionViolation, psycopg.errors.DeadlockDetected):
            raise Conflict(CONFLICT_MSG, "slot_conflict")
        if moved:
            await log_event(conn, clinic_id, appt_id, cur["status"], new_status, actor, {
                "moved_from": cur["start_at"].isoformat(), "moved_to": new_start.isoformat(),
                "doctor_changed": new_doc_id != cur["doctor_id"]})
        if changed_shape:  # moving cancels the old jobs and creates new ones (incl. a fresh confirmation)
            clinic = await _one(conn, "select * from clinic where id=%s", [clinic_id])
            await reminders.schedule_for_appointment(conn, row, clinic, now or utcnow())
        return row


async def set_status(conn, *, clinic_id: UUID, appt_id: UUID, to_status: str, actor: str = "reception",
                     now: datetime | None = None, detail: dict | None = None) -> dict:
    """Idempotent: setting the status an appointment already has is a no-op (no duplicate event)."""
    async with conn.transaction():
        cur = await get_appointment(conn, clinic_id, appt_id, for_update=True)
        if cur["status"] == to_status:
            return cur
        if to_status not in TRANSITIONS.get(cur["status"], set()):
            raise Invalid(f"Cannot change an appointment from {cur['status']} to {to_status}", "bad_transition")
        row = await _one(conn, "update appointment set status=%s where id=%s returning *", [to_status, appt_id])
        if to_status == "no_show":
            await conn.execute("update patient set no_show_count = no_show_count + 1 where id=%s", [cur["patient_id"]])
        await log_event(conn, clinic_id, appt_id, cur["status"], to_status, actor, detail)
        if to_status in ("cancelled", "no_show"):
            await reminders.cancel_scheduled(conn, appt_id)
        elif to_status == "arrived":
            await reminders.cancel_scheduled(conn, appt_id)
            clinic = await _one(conn, "select * from clinic where id=%s", [clinic_id])
            await reminders.schedule_post_visit(conn, row, clinic, now or utcnow())
        return row
