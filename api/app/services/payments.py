"""Optional booking deposit through a Razorpay payment link (Phase 8).

Flow: staff asks for a deposit -> we create a payment link (reference_id = appointment id) -> the patient gets it on WhatsApp
(template `showup_deposit_request`) -> Razorpay calls /webhooks/razorpay -> the appointment shows "Deposit paid".
Money never touches this system: it only creates links and reads signed webhooks. Refunds, forfeiting a deposit on no-show and
reconciliation are deliberately NOT built (they are policy decisions for the clinic and its accountant)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime
from uuid import UUID

import httpx
from psycopg.types.json import Jsonb

from .. import jobs
from ..config import Settings
from ..messaging import templates
from ..messaging.service import Outgoing
from ..reminders import fmt_date, fmt_time
from .appointments import Conflict, DomainError, Invalid, NotFound

log = logging.getLogger("showup.payments")
API = "https://api.razorpay.com/v1"
MIN_RUPEES, MAX_RUPEES, DEFAULT_RUPEES = 100, 50_000, 500
LINK_TTL_S = 48 * 3600


class PaymentsError(DomainError):
    status_code = 502


def configured(s: Settings) -> bool:
    return bool(s.razorpay_key_id and s.razorpay_key_secret)


def verify_signature(raw: bytes, header: str | None, secret: str) -> bool:
    """Razorpay signs the RAW body: hex HMAC-SHA256 with the webhook secret, in X-Razorpay-Signature."""
    if not secret or not header:
        return False
    return hmac.compare_digest(hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), header.strip())


async def create_link(s: Settings, *, amount_rupees: int, appointment_id: UUID, name: str, phone: str,
                      client: httpx.AsyncClient | None = None) -> dict:
    """POST /payment_links. Returns {"id","short_url"}. Description is deliberately generic (no treatment, nothing medical)."""
    body = {"amount": amount_rupees * 100, "currency": "INR", "accept_partial": False, "reference_id": str(appointment_id),
            "description": "Appointment booking deposit", "customer": {"name": name, "contact": phone},
            "notify": {"sms": False, "email": False}, "reminder_enable": False,
            "expire_by": int(time.time()) + LINK_TTL_S}
    auth = base64.b64encode(f"{s.razorpay_key_id}:{s.razorpay_key_secret}".encode()).decode()
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.post(f"{API}/payment_links", json=body, headers={"Authorization": f"Basic {auth}"})
    except httpx.HTTPError as e:
        raise PaymentsError(f"Could not reach Razorpay ({type(e).__name__})", "razorpay_unreachable")
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["description"]
        except Exception:
            msg = f"HTTP {r.status_code}"
        raise PaymentsError(f"Razorpay refused the link: {msg}", "razorpay_error")
    j = r.json()
    if not j.get("id") or not j.get("short_url"):
        raise PaymentsError("Razorpay returned an unexpected answer", "razorpay_error")
    return {"id": j["id"], "short_url": j["short_url"]}


def simulated_link() -> dict:
    """Demo mode without Razorpay keys (or for seeded patients): a clearly fake link so the flow can be shown."""
    token = uuid.uuid4().hex[:10]
    return {"id": f"plink_demo_{token}", "short_url": f"https://rzp.io/i/demo-{token}"}


def render(patient: dict, clinic: dict, appt: dict, amount: int, link: str):
    return templates.render("showup_deposit_request", patient["language"], {
        "patient_name": jobs.first_name(patient["name"]), "clinic_name": clinic["name"], "date": fmt_date(appt["start_at"]),
        "time": fmt_time(appt["start_at"]), "amount": amount, "payment_link": link})


async def request_deposit(pool, messaging, s: Settings, *, clinic_id: UUID, appointment_id: UUID, amount: int | None,
                          now: datetime, client: httpx.AsyncClient | None = None) -> dict:
    amount = amount or DEFAULT_RUPEES
    if not MIN_RUPEES <= amount <= MAX_RUPEES:
        raise Invalid(f"The deposit must be between ₹{MIN_RUPEES} and ₹{MAX_RUPEES:,}", "bad_amount")
    simulated = not configured(s)
    if simulated and not s.demo_mode:
        raise Conflict("Razorpay is not configured (set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)", "payments_not_configured")
    async with pool.connection() as conn:
        appt = await (await conn.execute("select * from appointment where id=%s and clinic_id=%s", [appointment_id, clinic_id])).fetchone()
        if not appt:
            raise NotFound("Appointment not found", "appointment_not_found")
        if appt["status"] not in ("pending", "confirmed", "rescheduled") or appt["start_at"] <= now:
            raise Conflict("A deposit can only be requested for an upcoming appointment that is still on", "not_upcoming")
        if appt["deposit_status"] == "paid":
            raise Conflict("The deposit for this appointment is already paid", "already_paid")
        patient = await (await conn.execute("select * from patient where id=%s", [appt["patient_id"]])).fetchone()
        clinic = await (await conn.execute("select * from clinic where id=%s", [clinic_id])).fetchone()
        if patient["opted_out"]:
            raise Conflict("This patient has opted out of messages", "opted_out")
    # Seeded patients are fictional: never create a real payment link for them, even when keys are configured.
    fake = simulated or bool(patient["is_demo_seed"])
    link = simulated_link() if fake else await create_link(
        s, amount_rupees=amount, appointment_id=appointment_id, name=patient["name"], phone=patient["phone"], client=client)
    async with pool.connection() as conn:
        await conn.execute(
            """update appointment set deposit_status='requested', deposit_amount=%s, payment_link_id=%s, payment_link_url=%s,
                      deposit_paid_at=null where id=%s""", [amount, link["id"], link["short_url"], appointment_id])
        await conn.execute("insert into appointment_event (appointment_id, clinic_id, from_status, to_status, actor, detail) "
                           "values (%s,%s,%s,%s,'reception',%s)",
                           [appointment_id, clinic_id, appt["status"], appt["status"], Jsonb({"deposit": "requested", "amount": amount})])
        res = await messaging.send(conn, clinic, Outgoing.from_rendered(render(patient, clinic, appt, amount, link["short_url"]),
                                                                       appointment_id=appointment_id),
                                   patient=patient, proactive=True, now=now)
    return {"appointment_id": appointment_id, "amount": amount, "payment_link_url": link["short_url"], "simulated": fake,
            "sent": res.outcome == "sent", "not_sent_reason": None if res.outcome == "sent" else res.reason}


async def mark_paid(conn, *, payment_link_id: str, amount_paise: int | None, now: datetime) -> str:
    """Idempotent. Returns 'paid' | 'already_paid' | 'unknown_link' | 'amount_mismatch'. An amount that differs from what we asked
    for is NOT accepted as paid (it is logged for a human to look at)."""
    appt = await (await conn.execute("select * from appointment where payment_link_id=%s for update", [payment_link_id])).fetchone()
    if not appt:
        return "unknown_link"
    if appt["deposit_status"] == "paid":
        return "already_paid"
    if amount_paise is not None and amount_paise != appt["deposit_amount"] * 100:
        log.error("deposit amount mismatch for appointment %s: paid %s paise, asked %s rupees", appt["id"], amount_paise, appt["deposit_amount"])
        return "amount_mismatch"
    await conn.execute("update appointment set deposit_status='paid', deposit_paid_at=%s where id=%s", [now, appt["id"]])
    await conn.execute("insert into appointment_event (appointment_id, clinic_id, from_status, to_status, actor, detail) "
                       "values (%s,%s,%s,%s,'system',%s)",
                       [appt["id"], appt["clinic_id"], appt["status"], appt["status"], Jsonb({"deposit": "paid", "amount": appt["deposit_amount"]})])
    return "paid"


async def handle_webhook(conn, payload: dict, now: datetime) -> str:
    """payment_link.paid / .expired / .cancelled. Other events are ignored (we answer 200 either way)."""
    event = payload.get("event", "")
    ent = ((payload.get("payload") or {}).get("payment_link") or {}).get("entity") or {}
    link_id = ent.get("id")
    if not link_id:
        return "ignored"
    if event == "payment_link.paid":
        paid = ent.get("amount_paid")
        return await mark_paid(conn, payment_link_id=link_id, amount_paise=int(paid) if paid is not None else None, now=now)
    if event in ("payment_link.expired", "payment_link.cancelled"):
        await conn.execute("update appointment set deposit_status='expired' where payment_link_id=%s and deposit_status='requested'", [link_id])
        return "expired"
    return "ignored"
