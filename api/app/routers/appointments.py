from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import schemas
from ..auth import Staff, current_staff
from ..db import get_conn
from ..config import get_settings
from ..services import appointments as svc
from ..services import payments
from ..services import waitlist
from ..services.slots import fetch_available_slots
from ..timeutil import ist_day_bounds, utcnow

router = APIRouter(tags=["appointments"])

LIST_SQL = """
select a.id, a.patient_id, a.doctor_id, a.treatment_type_id, a.start_at, a.end_at, a.duration_min,
       a.status, a.source, a.recovered_via_waitlist, a.internal_note,
       a.deposit_status, a.deposit_amount, a.payment_link_url, a.deposit_paid_at,
       p.name as patient_name, p.phone as patient_phone, p.language as patient_language,
       p.opted_out as patient_opted_out, p.no_show_count as patient_no_show_count,
       p.is_demo_seed as patient_is_demo_seed,
       d.name as doctor_name, d.colour as doctor_colour, t.name as treatment_name,
       le.to_status as last_event_status, le.actor as last_event_actor, le.created_at as last_event_at
from appointment a
join patient p on p.id = a.patient_id
join doctor d on d.id = a.doctor_id
left join treatment_type t on t.id = a.treatment_type_id
left join lateral (select to_status, actor, created_at from appointment_event e
                   where e.appointment_id = a.id order by created_at desc limit 1) le on true
"""

CALL_WINDOW_HOURS = 12


def _raise(e: svc.DomainError):
    raise HTTPException(e.status_code, {"code": e.code, "message": e.message})


@router.get("/me")
async def me(staff: Staff = Depends(current_staff)):
    return {"user_id": staff.user_id, "clinic_id": staff.clinic_id, "role": staff.role, "email": staff.email}


@router.get("/reference")
async def reference(staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    clinic = await (await conn.execute(
        "select id,name,address,maps_link,parking_note,phone,timezone,avg_ticket_value,baseline_no_show_rate,"
        "go_live_date,waitlist_offer_ttl_min,messaging_mode from clinic where id=%s", [staff.clinic_id])).fetchone()
    doctors = await (await conn.execute(
        "select id,name,colour,working_hours from doctor where clinic_id=%s and active order by name",
        [staff.clinic_id])).fetchall()
    treatments = await (await conn.execute(
        "select id,name,duration_min,recall_after_days,avg_ticket_value from treatment_type "
        "where clinic_id=%s order by name", [staff.clinic_id])).fetchall()
    return {"clinic": clinic, "doctors": doctors, "treatment_types": treatments}


@router.get("/slots")
async def slots(
    doctor_id: UUID, date: date, duration_min: int | None = Query(None, gt=0),
    treatment_type_id: UUID | None = None, exclude_appointment_id: UUID | None = None,
    staff: Staff = Depends(current_staff), conn=Depends(get_conn),
):
    """Available start times (UTC instants; the UI renders them in IST) for one doctor on one IST date."""
    try:
        doctor = await svc.get_doctor(conn, staff.clinic_id, doctor_id)
        if duration_min is None:
            if not treatment_type_id:
                raise svc.Invalid("Provide duration_min or treatment_type_id", "missing_duration")
            duration_min = (await svc.get_treatment(conn, staff.clinic_id, treatment_type_id))["duration_min"]
    except svc.DomainError as e:
        _raise(e)
    starts = await fetch_available_slots(conn, doctor, date, duration_min, utcnow(), exclude_appointment_id)
    return {"doctor_id": doctor_id, "date": date, "duration_min": duration_min, "slots": starts}


@router.get("/appointments")
async def list_appointments(
    date: date, doctor_id: UUID | None = None,
    staff: Staff = Depends(current_staff), conn=Depends(get_conn),
):
    lo, hi = ist_day_bounds(date)
    q = LIST_SQL + " where a.clinic_id=%s and a.start_at >= %s and a.start_at < %s"
    args: list = [staff.clinic_id, lo, hi]
    if doctor_id:
        q += " and a.doctor_id=%s"
        args.append(doctor_id)
    cur = await conn.execute(q + " order by a.start_at", args)
    return await cur.fetchall()


@router.get("/appointments/call-list")
async def call_list(hours: int = Query(CALL_WINDOW_HOURS, ge=1, le=48), staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    """Unconfirmed (still pending) appointments starting in the next `hours` hours: who reception should phone."""
    now = utcnow()
    cur = await conn.execute(LIST_SQL + """ where a.clinic_id=%s and a.status='pending' and a.start_at > %s
                                            and a.start_at <= %s order by a.start_at""",
                             [staff.clinic_id, now, now + timedelta(hours=hours)])
    return await cur.fetchall()


@router.get("/appointments/{appt_id}/log")
async def message_log(appt_id: UUID, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    """One timeline for an appointment: messages with their tick times, the patient's taps/replies, and status changes."""
    appt = await (await conn.execute("select id, patient_id from appointment where id=%s and clinic_id=%s",
                                     [appt_id, staff.clinic_id])).fetchone()
    if not appt:
        raise HTTPException(404, {"code": "appointment_not_found", "message": "Appointment not found"})
    out = await (await conn.execute(
        """select id, kind, template_name, language, rendered_body as body, footer, buttons, list, transport, wa_message_id,
                  status, error, created_at from outbound_message where appointment_id=%s order by created_at""", [appt_id])).fetchall()
    wamids = [m["wa_message_id"] for m in out if m["wa_message_id"]]
    ticks: dict[str, dict] = {}
    if wamids:
        for e in await (await conn.execute(
                """select wa_message_id, event, min(created_at) as at from message_event
                   where wa_message_id = any(%s) and event in ('sent','delivered','read','failed') group by 1,2""", [wamids])).fetchall():
            ticks.setdefault(e["wa_message_id"], {})[e["event"]] = e["at"]
    patient = await (await conn.execute("select phone from patient where id=%s", [appt["patient_id"]])).fetchone()
    replies = await (await conn.execute(
        """select id, event, payload, created_at from message_event
           where event in ('button','list_reply','text') and payload->>'from' = %s
             and (payload->>'context_wamid' = any(%s) or coalesce(payload->>'payload','') like %s)
           order by created_at""", [patient["phone"], wamids, f"%{appt_id}%"])).fetchall()
    changes = await (await conn.execute(
        "select id, from_status, to_status, actor, detail, created_at from appointment_event where appointment_id=%s", [appt_id])).fetchall()
    items = [{"type": "message", "at": m["created_at"], **m, "ticks": ticks.get(m["wa_message_id"], {})} for m in out]
    items += [{"type": "reply", "at": r["created_at"], "id": r["id"], "via": r["event"], "text": r["payload"].get("text"),
               "payload": r["payload"].get("payload")} for r in replies]
    items += [{"type": "status", "at": c["created_at"], **c} for c in changes]
    return sorted(items, key=lambda i: i["at"])


@router.get("/appointments/{appt_id}")
async def get_appointment(appt_id: UUID, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    cur = await conn.execute(LIST_SQL + " where a.id=%s and a.clinic_id=%s", [appt_id, staff.clinic_id])
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, {"code": "appointment_not_found", "message": "Appointment not found"})
    events = await (await conn.execute(
        "select from_status,to_status,actor,detail,created_at from appointment_event "
        "where appointment_id=%s order by created_at", [appt_id])).fetchall()
    return {**row, "events": events}


@router.post("/appointments", status_code=201)
async def create_appointment(body: schemas.AppointmentCreate, staff: Staff = Depends(current_staff),
                             conn=Depends(get_conn)):
    if bool(body.patient_id) == bool(body.new_patient):
        raise HTTPException(422, {"code": "patient_required",
                                  "message": "Provide exactly one of patient_id or new_patient"})
    try:
        patient_id = body.patient_id
        if body.new_patient:
            patient_id = (await svc.get_or_create_patient(conn, staff.clinic_id, body.new_patient.model_dump()))["id"]
        return await svc.create_appointment(
            conn, clinic_id=staff.clinic_id, patient_id=patient_id, doctor_id=body.doctor_id,
            treatment_type_id=body.treatment_type_id, start_at=body.start_at, source=body.source,
            internal_note=body.internal_note,
        )
    except svc.DomainError as e:
        _raise(e)


async def _offer_freed(request: Request, conn, clinic_id, appt: dict) -> None:
    """A slot just freed up (staff cancelled or moved the appointment): offer it to the waitlist."""
    clinic = await (await conn.execute("select * from clinic where id=%s", [clinic_id])).fetchone()
    await waitlist.offer_freed_slot(conn, request.app.state.messaging, clinic, appt["doctor_id"], appt["start_at"],
                                    appt["duration_min"], utcnow())


@router.patch("/appointments/{appt_id}")
async def update_appointment(appt_id: UUID, body: schemas.AppointmentUpdate, request: Request,
                             staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    try:
        prior = await svc.get_appointment(conn, staff.clinic_id, appt_id)
        row = await svc.move_appointment(
            conn, clinic_id=staff.clinic_id, appt_id=appt_id, start_at=body.start_at,
            doctor_id=body.doctor_id, treatment_type_id=body.treatment_type_id, internal_note=body.internal_note,
        )
        if (row["start_at"], row["doctor_id"]) != (prior["start_at"], prior["doctor_id"]):
            await _offer_freed(request, conn, staff.clinic_id, prior)          # the OLD slot goes to the waitlist
        return row
    except svc.DomainError as e:
        _raise(e)


@router.post("/appointments/{appt_id}/status")
async def change_status(appt_id: UUID, body: schemas.StatusChange, request: Request,
                        staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    try:
        prior = await svc.get_appointment(conn, staff.clinic_id, appt_id)
        row = await svc.set_status(conn, clinic_id=staff.clinic_id, appt_id=appt_id, to_status=body.status,
                                   actor=body.actor, detail={"via": body.via} if body.via else None)
        if body.status == "cancelled" and prior["status"] != "cancelled":
            await _offer_freed(request, conn, staff.clinic_id, prior)           # the freed slot goes to the waitlist
        return row
    except svc.DomainError as e:
        _raise(e)


class DepositBody(BaseModel):
    amount: int | None = Field(None, description="rupees; default 500")


@router.post("/appointments/{appt_id}/deposit")
async def request_deposit(appt_id: UUID, request: Request, body: DepositBody | None = None, staff: Staff = Depends(current_staff)):
    """Ask this patient for a booking deposit: creates a Razorpay payment link and sends it on WhatsApp."""
    try:
        return await payments.request_deposit(request.app.state.pool, request.app.state.messaging, get_settings(),
                                              clinic_id=staff.clinic_id, appointment_id=appt_id,
                                              amount=body.amount if body else None, now=utcnow())
    except svc.DomainError as e:
        _raise(e)
