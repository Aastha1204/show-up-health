"""Owner-only Settings. Everything an owner may change about the clinic without touching code or the database."""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from ..auth import Staff, require_role
from ..config import get_settings
from ..db import get_conn
from ..messaging import guards, templates
from ..phone import normalize_any_phone, normalize_phone
from ..services import whatsapp_health as wh
from ..services.slots import is_slot_valid
from ..timeutil import WEEKDAY_KEYS

router = APIRouter(prefix="/settings", tags=["settings"])
owner = require_role("owner")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
URL = re.compile(r"^https?://\S+$")
REMINDER_RULES = {          # type -> {field: (min, max)}; every type also has "enabled"
    "confirmation": {}, "recall": {}, "day_before": {"offset_min": (60, 7 * 1440)}, "two_hour": {"offset_min": (15, 1440)},
    "post_visit": {"offset_min": (0, 1440)}, "daily_summary": {},
}


def fail(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message})


def to_min(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:5])


async def _one(conn, sql, args=()):
    return await (await conn.execute(sql, list(args))).fetchone()


async def _all(conn, sql, args=()):
    return await (await conn.execute(sql, list(args))).fetchall()


async def check_message_text(conn, clinic_id, label: str, text: str | None, extra_treatments: list[str] = ()):
    """Text that ends up inside patient messages must pass the same medical-content guard as the messages themselves,
    or saving it would silently make every send fail."""
    if not text:
        return
    names = [r["name"] for r in await _all(conn, "select name from treatment_type where clinic_id=%s", [clinic_id])] + list(extra_treatments)
    hits = guards.find_medical_terms(text, names)
    if hits:
        fail(422, "medical_content", f"{label} appears in messages to patients, which must never mention treatments or medical terms (found: {', '.join(hits)}).")


# ───────────────────────── read ─────────────────────────
def fmt_clinic(c: dict) -> dict:
    out = {k: c[k] for k in ("id", "name", "address", "maps_link", "parking_note", "phone", "review_link", "timezone",
                             "avg_ticket_value", "go_live_date", "waitlist_offer_ttl_min", "reminder_settings", "messaging_mode")}
    out["baseline_no_show_rate"] = float(c["baseline_no_show_rate"])
    out["quiet_hours_start"], out["quiet_hours_end"] = c["quiet_hours_start"].strftime("%H:%M"), c["quiet_hours_end"].strftime("%H:%M")
    return out


async def doctor_rows(conn, clinic_id):
    docs = await _all(conn, """select d.id, d.name, d.whatsapp_phone, d.colour, d.working_hours, d.active, d.is_demo_seed, d.calendar_token,
        (select count(*) from appointment a where a.doctor_id=d.id and a.start_at > now()
           and a.status in ('pending','confirmed','rescheduled')) as upcoming_appointments
        from doctor d where d.clinic_id=%s order by d.active desc, d.name""", [clinic_id])
    return docs


@router.get("")
async def get_settings_(staff: Staff = Depends(owner), conn=Depends(get_conn)):
    c = await _one(conn, "select * from clinic where id=%s", [staff.clinic_id])
    tts = await _all(conn, "select id, name, duration_min, recall_after_days, avg_ticket_value from treatment_type where clinic_id=%s order by name", [staff.clinic_id])
    return {"clinic": fmt_clinic(c), "doctors": await doctor_rows(conn, staff.clinic_id), "treatment_types": tts}


# ───────────────────────── clinic details, quiet hours, reminders ─────────────────────────
class ClinicPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    address: str | None = Field(None, max_length=300)
    maps_link: str | None = None
    parking_note: str | None = Field(None, max_length=300)
    phone: str | None = None
    review_link: str | None = None
    avg_ticket_value: int | None = Field(None, ge=0, le=1_000_000)
    baseline_no_show_rate: float | None = Field(None, ge=0, le=1)
    go_live_date: date | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    waitlist_offer_ttl_min: int | None = Field(None, ge=1, le=120)
    reminder_settings: dict | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = " ".join(v.split()) if v else v
        if v is not None and not v:
            raise ValueError("Name cannot be blank")
        return v

    @field_validator("maps_link", "review_link")
    @classmethod
    def _url(cls, v):
        v = (v or "").strip()
        if v and not URL.match(v):
            raise ValueError("Enter a full link starting with https://")
        return v or None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        v = (v or "").strip()
        return normalize_any_phone(v) if v else None      # the clinic's own number may be a landline

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _hhmm(cls, v):
        if v is not None and not HHMM.match(v):
            raise ValueError("Use a 24-hour time like 21:00")
        return v


def validate_reminders(patch: dict, current: dict) -> dict:
    merged = {k: dict(v) for k, v in (current or {}).items()}
    for typ, vals in patch.items():
        if typ not in REMINDER_RULES or not isinstance(vals, dict):
            fail(422, "bad_reminder", f"Unknown reminder type “{typ}”.")
        allowed = {"enabled", *REMINDER_RULES[typ], *(["at"] if typ == "daily_summary" else [])}
        bad = set(vals) - allowed
        if bad:
            fail(422, "bad_reminder", f"{typ}: unknown setting(s) {', '.join(sorted(bad))}.")
        for k, v in vals.items():
            if k == "enabled" and not isinstance(v, bool):
                fail(422, "bad_reminder", f"{typ}.enabled must be true or false.")
            if k == "offset_min":
                lo, hi = REMINDER_RULES[typ]["offset_min"]
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    fail(422, "bad_reminder", f"{typ}: choose between {lo} and {hi} minutes.")
            if k == "at" and not (isinstance(v, str) and HHMM.match(v)):
                fail(422, "bad_reminder", "daily_summary.at must be a time like 08:00.")
        merged.setdefault(typ, {}).update(vals)
    return merged


@router.patch("/clinic")
async def patch_clinic(body: ClinicPatch, staff: Staff = Depends(owner), conn=Depends(get_conn)):
    data = body.model_dump(exclude_unset=True)
    if not data:
        fail(422, "empty", "Nothing to change.")
    if data.get("name") is None and "name" in data:
        fail(422, "required", "The clinic needs a name.")
    cur = await _one(conn, "select * from clinic where id=%s", [staff.clinic_id])
    for field, label in (("name", "The clinic name"), ("address", "The address"), ("parking_note", "The parking note")):
        await check_message_text(conn, staff.clinic_id, label, data.get(field))
    if "reminder_settings" in data:
        data["reminder_settings"] = validate_reminders(data["reminder_settings"] or {}, cur["reminder_settings"])
    for k in ("quiet_hours_start", "quiet_hours_end"):
        if k in data:
            data[k] = time.fromisoformat(data[k])
    sets = ", ".join(f"{k}=%s" for k in data)
    vals = [Jsonb(v) if k == "reminder_settings" else v for k, v in data.items()]
    await conn.execute(f"update clinic set {sets} where id=%s", [*vals, staff.clinic_id])
    return fmt_clinic(await _one(conn, "select * from clinic where id=%s", [staff.clinic_id]))


# ───────────────────────── doctors ─────────────────────────
def validate_hours(hours: dict) -> dict:
    out = {}
    for day, ranges in hours.items():
        if day not in WEEKDAY_KEYS:
            fail(422, "bad_hours", f"“{day}” is not a weekday (use mon, tue, ... sun).")
        if not isinstance(ranges, list):
            fail(422, "bad_hours", f"{day}: expected a list of time ranges.")
        prev_end = -1
        clean = []
        for r in sorted(ranges, key=lambda r: str(r[0]) if isinstance(r, (list, tuple)) and r else ""):
            if not (isinstance(r, (list, tuple)) and len(r) == 2 and all(isinstance(x, str) and HHMM.match(x) for x in r)):
                fail(422, "bad_hours", f"{day}: each range must be [\"HH:MM\", \"HH:MM\"].")
            a, b = to_min(r[0]), to_min(r[1])
            if b <= a:
                fail(422, "bad_hours", f"{day}: {r[0]}–{r[1]} ends before it starts.")
            if a % 15 or b % 15:
                fail(422, "bad_hours", f"{day}: times must be on the 15-minute grid ({r[0]}–{r[1]}).")
            if a < prev_end:
                fail(422, "bad_hours", f"{day}: ranges overlap.")
            prev_end = b
            clean.append([r[0], r[1]])
        out[day] = clean
    return {d: out.get(d, []) for d in WEEKDAY_KEYS}


class DoctorIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    whatsapp_phone: str | None = None
    colour: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    working_hours: dict | None = None
    active: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return " ".join(v.split()) if v else v

    @field_validator("whatsapp_phone")
    @classmethod
    def _p(cls, v):
        v = (v or "").strip()
        return normalize_phone(v) if v else None


async def outside_hours_count(conn, doctor_id, hours) -> int:
    rows = await _all(conn, """select start_at, duration_min from appointment where doctor_id=%s and start_at > now()
                               and status in ('pending','confirmed','rescheduled')""", [doctor_id])
    return sum(not is_slot_valid(hours, r["start_at"], r["duration_min"]) for r in rows)


async def _unique_doctor_name(conn, clinic_id, name, exclude=None):
    if await _one(conn, "select 1 as x from doctor where clinic_id=%s and lower(name)=lower(%s) and (%s::uuid is null or id <> %s)",
                  [clinic_id, name, exclude, exclude]):
        fail(409, "duplicate_name", f"There is already a doctor called “{name}”. Names must be different so imports and messages stay unambiguous.")


@router.post("/doctors", status_code=201)
async def add_doctor(body: DoctorIn, staff: Staff = Depends(owner), conn=Depends(get_conn)):
    if not body.name:
        fail(422, "required", "Enter the doctor's name.")
    await check_message_text(conn, staff.clinic_id, "The doctor's name", body.name)
    await _unique_doctor_name(conn, staff.clinic_id, body.name)
    hours = validate_hours(body.working_hours or {})
    row = await _one(conn, """insert into doctor (clinic_id, name, whatsapp_phone, colour, working_hours, active)
                              values (%s,%s,%s,%s,%s,true) returning id""",
                     [staff.clinic_id, body.name, body.whatsapp_phone, body.colour or "#0d9488", Jsonb(hours)])
    return next(d for d in await doctor_rows(conn, staff.clinic_id) if d["id"] == row["id"])


@router.patch("/doctors/{doctor_id}")
async def patch_doctor(doctor_id: UUID, body: DoctorIn, staff: Staff = Depends(owner), conn=Depends(get_conn)):
    cur = await _one(conn, "select * from doctor where id=%s and clinic_id=%s", [doctor_id, staff.clinic_id])
    if not cur:
        fail(404, "doctor_not_found", "Doctor not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        fail(422, "empty", "Nothing to change.")
    if "name" in data:
        if not data["name"]:
            fail(422, "required", "A doctor needs a name.")
        await check_message_text(conn, staff.clinic_id, "The doctor's name", data["name"])
        await _unique_doctor_name(conn, staff.clinic_id, data["name"], doctor_id)
    if data.get("active") is False and cur["active"]:
        others = await _one(conn, "select count(*) as n from doctor where clinic_id=%s and active and id <> %s", [staff.clinic_id, doctor_id])
        if others["n"] == 0:
            fail(409, "last_doctor", "At least one doctor must stay active.")
    if "working_hours" in data:
        data["working_hours"] = validate_hours(data["working_hours"] or {})
    sets = ", ".join(f"{k}=%s" for k in data)
    await conn.execute(f"update doctor set {sets} where id=%s", [Jsonb(v) if k == "working_hours" else v for k, v in data.items()] + [doctor_id])
    d = next(x for x in await doctor_rows(conn, staff.clinic_id) if x["id"] == doctor_id)
    # existing bookings are never touched; tell the owner how many now fall outside the new hours
    d["appointments_outside_hours"] = await outside_hours_count(conn, doctor_id, d["working_hours"]) if "working_hours" in data else 0
    return d


# ───────────────────────── treatment types (names are internal; never sent to patients) ─────────────────────────
class TreatmentIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    duration_min: int | None = Field(None, ge=5, le=240)
    recall_after_days: int | None = Field(None, ge=1, le=1095)
    avg_ticket_value: int | None = Field(None, ge=0, le=1_000_000)

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return " ".join(v.split()) if v else v

    @field_validator("duration_min")
    @classmethod
    def _d(cls, v):
        if v is not None and v % 5:
            raise ValueError("Use a multiple of 5 minutes")
        return v


async def _check_treatment_name(conn, clinic_id, name, exclude=None):
    if await _one(conn, "select 1 as x from treatment_type where clinic_id=%s and lower(name)=lower(%s) and (%s::uuid is null or id <> %s)",
                  [clinic_id, name, exclude, exclude]):
        fail(409, "duplicate_name", f"There is already a treatment called “{name}”.")
    # a treatment name that also appears in patient-facing text would make the guard block every message
    c = await _one(conn, "select name, address, parking_note from clinic where id=%s", [clinic_id])
    docs = [d["name"] for d in await _all(conn, "select name from doctor where clinic_id=%s", [clinic_id])]
    visible = " ".join(filter(None, [c["name"], c["address"], c["parking_note"], *docs]))
    if guards.find_medical_terms(visible, [name]):
        fail(422, "collides_with_messages", f"“{name}” appears in the clinic name, address, parking note or a doctor's name, so using it as a "
                                             "treatment name would make the medical-content guard block every message to patients.")


@router.post("/treatments", status_code=201)
async def add_treatment(body: TreatmentIn, staff: Staff = Depends(owner), conn=Depends(get_conn)):
    if not body.name or not body.duration_min:
        fail(422, "required", "A treatment needs a name and a duration.")
    await _check_treatment_name(conn, staff.clinic_id, body.name)
    return await _one(conn, """insert into treatment_type (clinic_id, name, duration_min, recall_after_days, avg_ticket_value)
                               values (%s,%s,%s,%s,%s) returning id, name, duration_min, recall_after_days, avg_ticket_value""",
                      [staff.clinic_id, body.name, body.duration_min, body.recall_after_days, body.avg_ticket_value or 0])


@router.patch("/treatments/{tid}")
async def patch_treatment(tid: UUID, body: TreatmentIn, staff: Staff = Depends(owner), conn=Depends(get_conn)):
    if not await _one(conn, "select 1 as x from treatment_type where id=%s and clinic_id=%s", [tid, staff.clinic_id]):
        fail(404, "treatment_not_found", "Treatment not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        fail(422, "empty", "Nothing to change.")
    if "name" in data:
        if not data["name"]:
            fail(422, "required", "A treatment needs a name.")
        await _check_treatment_name(conn, staff.clinic_id, data["name"], tid)
    if data.get("duration_min") is None and "duration_min" in data:
        fail(422, "required", "A treatment needs a duration.")
    sets = ", ".join(f"{k}=%s" for k in data)
    return await _one(conn, f"update treatment_type set {sets} where id=%s returning id, name, duration_min, recall_after_days, avg_ticket_value",
                      [*data.values(), tid])


# ───────────────────────── template preview + submission ─────────────────────────
@router.get("/templates")
async def preview_templates(language: Literal["en", "hi", "mr"] = "en", staff: Staff = Depends(owner)):
    """Each template as a patient would see it (sample values). A language with no approved text falls back to English."""
    out = []
    for name, t in sorted(templates.load_all().items()):
        r = templates.render(name, language, templates.sample_values(name, language),
                             ids={"appointment_id": "…", "patient_id": "…", "offer_id": "…"})
        out.append({"name": name, "category": t["category"], "category_note": t.get("category_note"), "recipient": t.get("recipient", "patient"),
                    "language": r.language, "fallback": r.language != language, "body": r.body, "footer": r.footer,
                    "buttons": [b["text"] for b in r.buttons], "variables": t["variables"], "languages": sorted(t["languages"])})
    return out


class Submit(BaseModel):
    language: Literal["en", "hi", "mr"] = "en"


@router.post("/templates/submit")
async def submit_templates(body: Submit, request: Request, staff: Staff = Depends(owner)):
    s = get_settings()
    if not (s.wa_access_token and s.wa_business_account_id):
        fail(409, "not_configured", "Set WA_ACCESS_TOKEN and WA_BUSINESS_ACCOUNT_ID first.")
    return {"language": body.language, "results": await wh.submit_templates(s, body.language, getattr(request.app.state, "graph_client", None))}


# ───────────────────────── WhatsApp health + messaging mode ─────────────────────────
async def _health(request: Request, conn, refresh: bool) -> dict:
    s = get_settings()
    clinic = await _one(conn, "select messaging_mode from clinic order by created_at limit 1")
    last = await _one(conn, "select value, updated_at from system_status where key='last_webhook'")
    h = await wh.check(s, mode=clinic["messaging_mode"], last_webhook=last, client=getattr(request.app.state, "graph_client", None), refresh=refresh)
    h["webhook_path"] = "/webhooks/whatsapp"
    h["webhook_url"] = f"{s.public_api_url.rstrip('/')}/webhooks/whatsapp" if s.public_api_url else None
    h["api_version"] = s.wa_api_version
    return h


@router.get("/health")
async def health(request: Request, refresh: bool = Query(False), staff: Staff = Depends(owner), conn=Depends(get_conn)):
    return await _health(request, conn, refresh)


class ModeIn(BaseModel):
    mode: Literal["live", "simulator"]


@router.put("/messaging-mode")
async def set_mode(body: ModeIn, request: Request, staff: Staff = Depends(owner), conn=Depends(get_conn)):
    """Simulator is always allowed (it is the fallback). Live only when the health check has no blockers."""
    if body.mode == "live":
        h = await _health(request, conn, refresh=True)
        if h["blockers"]:
            raise HTTPException(409, {"code": "live_blocked", "message": "Live WhatsApp is not ready: " + " ".join(h["blockers"]), "blockers": h["blockers"]})
    await conn.execute("update clinic set messaging_mode=%s where id=%s", [body.mode, staff.clinic_id])
    return {"mode": body.mode}


@router.post("/doctors/{doctor_id}/calendar/reset")
async def reset_calendar_link(doctor_id: UUID, staff: Staff = Depends(owner), conn=Depends(get_conn)):
    """New private calendar URL; the old one stops working at once (use it if the link was shared by mistake)."""
    row = await _one(conn, """update doctor set calendar_token = replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '')
                              where id=%s and clinic_id=%s returning calendar_token""", [doctor_id, staff.clinic_id])
    if not row:
        fail(404, "doctor_not_found", "Doctor not found")
    return {"calendar_token": row["calendar_token"]}
