"""Demo-mode helpers: 'Fire now' for reminder jobs, and a local-only dev login (no Supabase needed)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Literal
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import jobs, seed
from ..config import get_settings
from ..db import get_conn
from ..messaging import templates
from ..messaging.service import Outgoing
from ..phone import normalize_phone
from ..services import appointments as svc
from ..services import payments, waitlist
from ..services.slots import compute_slots, fetch_busy
from ..timeutil import ist_today, utcnow
from .simulator import demo_staff

router = APIRouter(tags=["demo"])
_NS = uuid.UUID("5f0c0a3e-0000-4000-8000-000000000001")


class FireBody(BaseModel):
    appointment_id: UUID
    type: Literal["confirmation", "day_before", "two_hour", "post_visit"]


@router.post("/demo/fire")
async def fire_now(body: FireBody, request: Request, staff=Depends(demo_staff), conn=Depends(get_conn)):
    """Create the job due now and run it immediately, skipping timing/quiet-hours/already-confirmed checks only."""
    appt = await (await conn.execute("select * from appointment where id=%s and clinic_id=%s",
                                     [body.appointment_id, staff.clinic_id])).fetchone()
    if not appt:
        raise HTTPException(404, "Appointment not found")
    now = utcnow()
    job = await (await conn.execute(
        """insert into reminder_job (clinic_id, appointment_id, patient_id, doctor_id, type, due_at)
           values (%s,%s,%s,%s,%s,%s) returning *""",
        [staff.clinic_id, appt["id"], appt["patient_id"], appt["doctor_id"], body.type, now])).fetchone()
    outcome = await jobs.execute_job(conn, request.app.state.messaging, job, now=now, force=True)
    await jobs._finish(conn, job, outcome, now)
    return {"job_id": job["id"], "status": outcome.status, "reason": outcome.reason,
            "outbound_message_id": outcome.outbound_id}


class DevLogin(BaseModel):
    role: Literal["owner", "reception"] = "reception"


@router.post("/auth/dev-login")
async def dev_login(body: DevLogin, conn=Depends(get_conn)):
    """Local development only: mints a token for a seeded demo user. Refuses unless DEMO_MODE is on, a local JWT
    secret is set, and no Supabase project is configured (so it can never exist next to real auth)."""
    s = get_settings()
    if not s.demo_mode or s.supabase_url or not s.supabase_jwt_secret:
        raise HTTPException(404, "Not found")
    clinic = await (await conn.execute("select id from clinic order by created_at limit 1")).fetchone()
    if not clinic:
        raise HTTPException(409, "Seed the database first (python -m app.seed)")
    email = f"{body.role}@smilecare.demo"
    uid = uuid.uuid5(_NS, email)
    await conn.execute(
        """insert into staff_profile (clinic_id, user_id, email, role) values (%s,%s,%s,%s)
           on conflict (user_id) do update set clinic_id=excluded.clinic_id, role=excluded.role""",
        [clinic["id"], uid, email, body.role])
    token = jwt.encode({"sub": str(uid), "aud": "authenticated", "email": email}, s.supabase_jwt_secret, "HS256")
    return {"access_token": token, "role": body.role, "email": email}


# ───────────────────────── presenter controls (/demo) ─────────────────────────
async def _clinic(conn, clinic_id):
    return await (await conn.execute("select * from clinic where id=%s", [clinic_id])).fetchone()


def live_ready() -> bool:
    """Live sending needs credentials AND the Live transport, which is built in the go-live phase."""
    s = get_settings()
    from ..messaging.transport import LiveTransport
    return bool(s.wa_access_token and s.wa_phone_number_id and getattr(LiveTransport, "implemented", False))


async def _cancel_me(conn, clinic_id):
    return await (await conn.execute(
        """select a.id, a.start_at, a.duration_min, a.doctor_id, p.name as patient_name from appointment a
           join patient p on p.id = a.patient_id where a.clinic_id=%s and a.internal_note like 'DEMO: cancel me%%'
           and a.status in ('pending','confirmed','rescheduled') and a.start_at > now() order by a.start_at limit 1""", [clinic_id])).fetchone()


@router.get("/demo/state")
async def demo_state(staff=Depends(demo_staff), conn=Depends(get_conn)):
    clinic = await _clinic(conn, staff.clinic_id)
    cm = await _cancel_me(conn, staff.clinic_id)
    nxt = None
    if cm:
        nxt = await (await conn.execute(
            """select p.name from waitlist_entry e join patient p on p.id = e.patient_id where e.clinic_id=%s and e.status='waiting'
               and e.preferred_date = (%s at time zone 'Asia/Kolkata')::date and (e.doctor_id is null or e.doctor_id=%s)
               and not p.opted_out order by e.created_at limit 1""", [staff.clinic_id, cm["start_at"], cm["doctor_id"]])).fetchone()
    return {"mode": clinic["messaging_mode"], "live_ready": live_ready(), "waitlist_ttl_min": clinic["waitlist_offer_ttl_min"],
            "cancel_me": {"appointment_id": cm["id"], "patient_name": cm["patient_name"], "start_at": cm["start_at"],
                          "next_waitlist_patient": nxt["name"] if nxt else None} if cm else None}


class Prospect(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str
    language: Literal["en", "hi", "mr"] = "en"

    @field_validator("phone")
    @classmethod
    def _p(cls, v: str) -> str:
        return normalize_phone(v)


@router.post("/demo/prospect")
async def create_prospect(body: Prospect, request: Request, staff=Depends(demo_staff), conn=Depends(get_conn)):
    """Book the prospect for TOMORROW (the next working day) at the earliest free slot, and send the confirmation now.
    In Live mode that lands on the prospect's real phone; in Simulator mode it appears in their simulator chat."""
    clinic = await _clinic(conn, staff.clinic_id)
    now = utcnow()
    doctors = await (await conn.execute("select * from doctor where clinic_id=%s and active order by name", [staff.clinic_id])).fetchall()
    tt = await (await conn.execute("select * from treatment_type where clinic_id=%s order by (name='Consultation') desc, duration_min limit 1",
                                   [staff.clinic_id])).fetchone()
    pick = None
    for i in range(1, 9):                                   # tomorrow, or the next working day after it
        day = ist_today(now) + timedelta(days=i)
        options = []
        for d in doctors:
            for s in compute_slots(d["working_hours"], day, tt["duration_min"], await fetch_busy(conn, d["id"], day), now):
                options.append((s, d["name"], d))
        options.sort(key=lambda o: (o[0], o[1]))
        # The earliest slot that NO waitlisted patient is waiting for. Rescheduling the prospect later frees this slot,
        # and the waitlist engine would (rightly) offer it at once; the demo wants the presenter to decide when the
        # first offer goes out (step 4: cancel the seeded appointment), so the prospect must not sit in someone's slot.
        for o in options:
            if not await waitlist.candidates(conn, staff.clinic_id, o[2]["id"], o[0]):
                pick = o
                break
        pick = pick or (options[0] if options else None)
        if pick:
            break
    if not pick:
        raise HTTPException(409, {"code": "no_slots", "message": "No free slot in the next week. Reset the demo."})
    start, _, doctor = pick
    patient = await svc.get_or_create_patient(conn, staff.clinic_id, {
        "name": " ".join(body.name.split()), "phone": body.phone, "language": body.language, "consent_source": "reception"})
    appt = await svc.create_appointment(conn, clinic_id=staff.clinic_id, patient_id=patient["id"], doctor_id=doctor["id"],
                                        treatment_type_id=tt["id"], start_at=start, source="dashboard", now=now)
    job = await (await conn.execute(
        """select * from reminder_job where appointment_id=%s and type='confirmation' and status='scheduled' limit 1 for update""",
        [appt["id"]])).fetchone()
    outcome = None
    if job:                                                 # the confirmation goes out right now, not at the next tick
        outcome = await jobs.execute_job(conn, request.app.state.messaging, job, now=now, force=True)
        await jobs._finish(conn, job, outcome, now)
    return {"appointment_id": appt["id"], "patient_id": patient["id"], "patient_name": patient["name"], "doctor_name": doctor["name"],
            "start_at": appt["start_at"], "mode": clinic["messaging_mode"],
            "confirmation": outcome.status if outcome else "none", "confirmation_reason": outcome.reason if outcome else None}


@router.post("/demo/cancel-seeded")
async def cancel_seeded(request: Request, staff=Depends(demo_staff), conn=Depends(get_conn)):
    """Cancel the seeded 'cancel me' appointment as reception would; the freed slot is offered to the waitlist."""
    cm = await _cancel_me(conn, staff.clinic_id)
    if not cm:
        raise HTTPException(404, {"code": "nothing_to_cancel", "message": "The seeded appointment is already cancelled. Reset the demo to restore it."})
    await svc.set_status(conn, clinic_id=staff.clinic_id, appt_id=cm["id"], to_status="cancelled", actor="reception")
    clinic = await _clinic(conn, staff.clinic_id)
    await waitlist.offer_freed_slot(conn, request.app.state.messaging, clinic, cm["doctor_id"], cm["start_at"], cm["duration_min"], utcnow())
    offer = await (await conn.execute(
        """select p.name as patient_name, p.id as patient_id, o.id as offer_id, o.expires_at from waitlist_offer o join waitlist_entry e on e.id=o.waitlist_entry_id
           join patient p on p.id=e.patient_id where o.doctor_id=%s and o.slot_start_at=%s and o.status='pending'""",
        [cm["doctor_id"], cm["start_at"]])).fetchone()
    return {"cancelled_patient": cm["patient_name"], "start_at": cm["start_at"], "offered_to": offer["patient_name"] if offer else None,
            "offered_patient_id": offer["patient_id"] if offer else None,
            "offer_id": offer["offer_id"] if offer else None, "offer_expires_at": offer["expires_at"] if offer else None}


class Summary(BaseModel):
    doctor_id: UUID


@router.post("/demo/summary")
async def send_summary(body: Summary, request: Request, staff=Depends(demo_staff), conn=Depends(get_conn)):
    """Send the doctor's 8 a.m. summary now, for the first working day from today (so a Sunday demo shows Monday)."""
    clinic = await _clinic(conn, staff.clinic_id)
    doctor = await (await conn.execute("select * from doctor where id=%s and clinic_id=%s and active", [body.doctor_id, staff.clinic_id])).fetchone()
    if not doctor:
        raise HTTPException(404, "Doctor not found")
    now = utcnow()
    from ..timeutil import weekday_key
    day = next((ist_today(now) + timedelta(days=i) for i in range(8) if (doctor["working_hours"] or {}).get(weekday_key(ist_today(now) + timedelta(days=i)))), ist_today(now))
    values = await jobs.summary_values(conn, clinic, doctor, now, day)
    r = templates.render("showup_daily_summary", "en", values)
    res = await request.app.state.messaging.send(conn, clinic, Outgoing.from_rendered(r),
                                                 doctor=doctor, proactive=True, ignore_quiet_hours=True, now=now)
    return {"status": res.outcome, "reason": res.reason, "doctor_id": doctor["id"], "body": r.body}


class Ttl(BaseModel):
    minutes: int = Field(ge=1, le=60)


@router.put("/demo/ttl")
async def set_ttl(body: Ttl, staff=Depends(demo_staff), conn=Depends(get_conn)):
    """Waitlist offer lifetime, so a demo can show an offer expiring in 2 minutes instead of 15."""
    await conn.execute("update clinic set waitlist_offer_ttl_min=%s where id=%s", [body.minutes, staff.clinic_id])
    return {"waitlist_ttl_min": body.minutes}


class Mode(BaseModel):
    mode: Literal["live", "simulator"]


@router.put("/demo/mode")
async def set_mode(body: Mode, staff=Depends(demo_staff), conn=Depends(get_conn)):
    """One-click Live/Simulator switch. Simulator is always allowed (the network-failure fallback); Live only when ready."""
    if body.mode == "live" and not live_ready():
        raise HTTPException(409, {"code": "live_unavailable", "message":
                                  "Live WhatsApp is not available: set WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID, and the Live transport must be built (go-live phase)."})
    await conn.execute("update clinic set messaging_mode=%s where id=%s", [body.mode, staff.clinic_id])
    return {"mode": body.mode}


@router.post("/demo/reset")
async def reset_demo(staff=Depends(demo_staff), conn=Depends(get_conn)):
    """Wipe and re-seed relative to the current IST date and time. Staff logins and the messaging mode survive."""
    mode = (await _clinic(conn, staff.clinic_id))["messaging_mode"]
    await conn.commit()                                     # release this request's snapshot before the seed truncates
    ds = await asyncio.to_thread(seed.load, get_settings().database_url)
    await conn.execute("update clinic set messaging_mode=%s", [mode])
    return {"patients": len(ds["patients"]), "appointments": len(ds["appointments"]), "mode": mode}


@router.post("/demo/deposit/{appt_id}/pay")
async def simulate_deposit_paid(appt_id: UUID, staff=Depends(demo_staff), conn=Depends(get_conn)):
    """Presenter button: 'the patient paid'. Runs the same handler the real Razorpay webhook runs; only works on simulated links."""
    a = await (await conn.execute("select deposit_status, deposit_amount, payment_link_id from appointment where id=%s and clinic_id=%s",
                                  [appt_id, staff.clinic_id])).fetchone()
    if not a or a["deposit_status"] != "requested" or not (a["payment_link_id"] or "").startswith("plink_demo_"):
        raise HTTPException(409, {"code": "no_simulated_deposit", "message": "There is no simulated deposit waiting on this appointment"})
    result = await payments.handle_webhook(conn, {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {
        "id": a["payment_link_id"], "amount_paid": a["deposit_amount"] * 100}}}}, utcnow())
    return {"result": result}
