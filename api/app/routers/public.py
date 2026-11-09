"""Unauthenticated endpoints behind the public booking page / the clinic website's form.

Defences: Origin allow-list (PUBLIC_BOOKING_ALLOWED_ORIGINS), per-address rate limit, a honeypot field, mandatory
consent, and responses that never reveal whether a phone number is already a patient (or opted out)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from typing import Literal

from ..config import get_settings
from ..db import get_conn
from ..phone import normalize_phone
from ..ratelimit import booking_limiter, client_ip, slots_limiter
from ..services import appointments as svc
from ..services.slots import compute_slots, fetch_busy
from ..timeutil import ist_today, utcnow

router = APIRouter(prefix="/public", tags=["public"])

MIN_LEAD = timedelta(hours=2)          # no same-minute walk-ins through the website
MAX_DAYS_AHEAD = 60
CONSENT_TEXT = "I agree to receive appointment updates on WhatsApp."


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().cors_origins:
        raise HTTPException(403, {"code": "origin_not_allowed", "message": "This website is not allowed to book here."})


def _limit(limiter, request: Request) -> None:
    retry = limiter.check(client_ip(request))
    if retry is not None:
        raise HTTPException(429, {"code": "rate_limited", "message": "Too many attempts. Please wait a moment and try again."},
                            headers={"Retry-After": str(retry)})


async def _clinic(conn) -> dict:
    row = await (await conn.execute("select * from clinic order by created_at limit 1")).fetchone()
    if not row:
        raise HTTPException(503, {"code": "not_ready", "message": "Booking is not available right now."})
    return row


@router.get("/clinic")
async def clinic_info(request: Request, conn=Depends(get_conn)):
    """What the booking form needs: clinic name, doctors and bookable services. No patient data."""
    _check_origin(request)
    c = await _clinic(conn)
    docs = await (await conn.execute("select id, name from doctor where clinic_id=%s and active order by name", [c["id"]])).fetchall()
    tts = await (await conn.execute("select id, name, duration_min from treatment_type where clinic_id=%s order by name", [c["id"]])).fetchall()
    return {"name": c["name"], "address": c["address"], "consent_text": CONSENT_TEXT,
            "max_days_ahead": MAX_DAYS_AHEAD, "doctors": docs, "treatments": tts}


@router.get("/slots")
async def public_slots(request: Request, date: date, treatment_type_id: UUID, doctor_id: UUID | None = None,
                       conn=Depends(get_conn)):
    """Free start times for a day. With no doctor chosen, a time is offered if ANY doctor is free then."""
    _check_origin(request)
    _limit(slots_limiter, request)
    c = await _clinic(conn)
    today = ist_today()
    if date < today or date > today + timedelta(days=MAX_DAYS_AHEAD):
        return {"date": date, "slots": []}
    try:
        tt = await svc.get_treatment(conn, c["id"], treatment_type_id)
        docs = [await svc.get_doctor(conn, c["id"], doctor_id)] if doctor_id else await (await conn.execute(
            "select * from doctor where clinic_id=%s and active order by name", [c["id"]])).fetchall()
    except svc.DomainError as e:
        raise HTTPException(e.status_code, {"code": e.code, "message": e.message})
    found: set[datetime] = set()
    for d in docs:
        busy = await fetch_busy(conn, d["id"], date)
        found.update(compute_slots(d["working_hours"], date, tt["duration_min"], busy, utcnow(), min_lead=MIN_LEAD))
    return {"date": date, "duration_min": tt["duration_min"], "slots": sorted(found)}


class PublicBooking(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str
    language: Literal["en", "hi", "mr"] = "en"
    doctor_id: UUID | None = None            # None = any doctor
    treatment_type_id: UUID
    start_at: AwareDatetime
    consent: bool
    website: str | None = None               # HONEYPOT: hidden from people, filled in by bots

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Please enter your name")
        return v

    @field_validator("consent")
    @classmethod
    def _consent(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError(f"Please tick the box: \"{CONSENT_TEXT}\"")
        return v


@router.post("/appointments", status_code=201)
async def book(body: PublicBooking, request: Request, conn=Depends(get_conn)):
    _check_origin(request)
    _limit(booking_limiter, request)
    if body.website:                                   # a bot: look successful, create nothing
        return {"ok": True}
    c = await _clinic(conn)
    now = utcnow()
    if body.start_at < now + MIN_LEAD or (body.start_at - now) > timedelta(days=MAX_DAYS_AHEAD + 1):
        raise HTTPException(422, {"code": "bad_time", "message": "Please choose a time at least 2 hours from now, within the next 60 days."})
    try:
        tt = await svc.get_treatment(conn, c["id"], body.treatment_type_id)
        doctors = [await svc.get_doctor(conn, c["id"], body.doctor_id)] if body.doctor_id else await (await conn.execute(
            "select * from doctor where clinic_id=%s and active order by name", [c["id"]])).fetchall()
        patient = await svc.get_or_create_patient(conn, c["id"], {
            "name": body.name, "phone": body.phone, "language": body.language, "consent_source": "website_form"})
        last: svc.DomainError | None = None
        for doc in doctors:                            # "any doctor": the first one who is actually free at that time
            try:
                appt = await svc.create_appointment(
                    conn, clinic_id=c["id"], patient_id=patient["id"], doctor_id=doc["id"], treatment_type_id=tt["id"],
                    start_at=body.start_at, source="website", actor="patient", now=now)
                return {"ok": True, "start_at": appt["start_at"], "doctor_name": doc["name"], "clinic_name": c["name"]}
            except svc.Conflict as e:
                last = e
            except svc.Invalid as e:
                last = e
        if isinstance(last, svc.Conflict) or last is None:
            raise HTTPException(409, {"code": "slot_conflict", "message": "Sorry, that time was just taken. Please choose another."})
        raise HTTPException(422, {"code": last.code, "message": "That time is not available. Please choose another."})
    except svc.DomainError as e:
        raise HTTPException(e.status_code, {"code": e.code, "message": e.message})
