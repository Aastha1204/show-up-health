from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator

from ..auth import Staff, current_staff
from ..db import get_conn
from ..schemas import PatientIn
from ..services import appointments as appt_svc
from ..services import waitlist
from ..timeutil import ist_today, utcnow

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

LIST_SQL = """
select e.id, e.patient_id, p.name as patient_name, p.phone as patient_phone, e.doctor_id,
       d.name as doctor_name, e.treatment_type_id, t.name as treatment_name, e.preferred_date, e.preferred_window,
       e.status, e.created_at,
       o.id as offer_id, o.slot_start_at as offer_slot_start_at, o.expires_at as offer_expires_at,
       o.doctor_id as offer_doctor_id
from waitlist_entry e
join patient p on p.id = e.patient_id
left join doctor d on d.id = e.doctor_id
left join treatment_type t on t.id = e.treatment_type_id
left join waitlist_offer o on o.waitlist_entry_id = e.id and o.status = 'pending'
"""


class WaitlistCreate(BaseModel):
    patient_id: UUID | None = None
    new_patient: PatientIn | None = None
    doctor_id: UUID | None = None           # null = any doctor
    treatment_type_id: UUID | None = None
    preferred_date: date
    preferred_window: Literal["morning", "afternoon", "evening", "any"] = "any"

    @model_validator(mode="after")
    def _one_patient(self):
        if bool(self.patient_id) == bool(self.new_patient):
            raise ValueError("Provide exactly one of patient_id or new_patient")
        return self


@router.get("")
async def list_entries(date: date | None = None, include_closed: bool = False,
                       staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    """Waiting/offered entries (upcoming dates by default). A pending offer carries offer_expires_at for the countdown."""
    q = LIST_SQL + " where e.clinic_id=%s"
    args: list = [staff.clinic_id]
    if date:
        q += " and e.preferred_date=%s"
        args.append(date)
    else:
        q += " and e.preferred_date >= %s"
        args.append(ist_today())
    if not include_closed:
        q += " and e.status in ('waiting','offered')"
    return await (await conn.execute(q + " order by e.preferred_date, e.created_at, e.id", args)).fetchall()


@router.post("", status_code=201)
async def add_entry(body: WaitlistCreate, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    try:
        if body.doctor_id:
            await appt_svc.get_doctor(conn, staff.clinic_id, body.doctor_id)
        if body.treatment_type_id:
            await appt_svc.get_treatment(conn, staff.clinic_id, body.treatment_type_id)
        patient_id = body.patient_id
        if body.new_patient:
            patient_id = (await appt_svc.get_or_create_patient(conn, staff.clinic_id, body.new_patient.model_dump()))["id"]
        elif not await (await conn.execute("select 1 as x from patient where id=%s and clinic_id=%s",
                                           [patient_id, staff.clinic_id])).fetchone():
            raise appt_svc.NotFound("Patient not found", "patient_not_found")
    except appt_svc.DomainError as e:
        raise HTTPException(e.status_code, {"code": e.code, "message": e.message})
    row = await (await conn.execute(
        """insert into waitlist_entry (clinic_id, patient_id, doctor_id, treatment_type_id, preferred_date, preferred_window)
           values (%s,%s,%s,%s,%s,%s) returning *""",
        [staff.clinic_id, patient_id, body.doctor_id, body.treatment_type_id, body.preferred_date,
         body.preferred_window])).fetchone()
    return row


@router.delete("/{entry_id}")
async def remove_entry(entry_id: UUID, request: Request, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    clinic = await (await conn.execute("select * from clinic where id=%s", [staff.clinic_id])).fetchone()
    if not await waitlist.remove_entry(conn, request.app.state.messaging, clinic, entry_id, utcnow()):
        raise HTTPException(404, {"code": "entry_not_found", "message": "Waitlist entry not found or already closed"})
    return {"removed": True}
