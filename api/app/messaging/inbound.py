"""Patient actions (spec 7.3). Called by the webhook handler for every deduped inbound event, inside its transaction,
so a failure rolls back the action AND the dedupe row, and Meta's retry can safely run it again.

Idempotent by construction: state is decided under a row lock, so repeated or concurrent taps are no-ops."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from .. import jobs
from ..reminders import fmt_date, fmt_time
from ..services import appointments as svc
from ..services import reschedule, waitlist
from ..timeutil import utcnow
from . import optout
from .service import Outgoing

log = logging.getLogger("showup.inbound")

ACTIVE = ("pending", "confirmed", "rescheduled")
ACK_TEXT = "Thanks, our reception team will reply shortly."
ACK_EVERY = timedelta(hours=12)
INACTIVE_TEXT = "This appointment is no longer active. Reply here if you need help."
CANCELLED_TEXT = "Your appointment has been cancelled. Reply here any time to book again."
TAKEN_TEXT = "Sorry, that time was just taken. Here are the latest options."
STALE_TEXT = "Those times have changed. Here are the latest options."
NO_SLOTS_TEXT = "Sorry, we have no free times in the next few days. Our reception team will contact you shortly."
# Template buttons whose payload is missing fall back to the button's visible text (case-insensitive).
TEXT_TO_VERB = {"confirm": "CONFIRM", "reschedule": "RESCHEDULE", "cancel": "CANCEL"}


class Ctx:
    def __init__(self, conn, messaging, clinic, patient, now):
        self.conn, self.messaging, self.clinic, self.patient, self.now = conn, messaging, clinic, patient, now

    async def one(self, sql, args):
        return await (await self.conn.execute(sql, args)).fetchone()

    async def reply(self, text: str, appt_id=None, **kw):
        """Session reply to the patient's own message (never subject to quiet hours)."""
        return await self.messaging.send(
            self.conn, self.clinic, Outgoing("text", text, self.patient["language"], appointment_id=appt_id),
            patient=self.patient, proactive=False, now=self.now, **kw)

    async def inbox(self, text: str):
        await self.conn.execute("insert into inbox_item (clinic_id, patient_id, text, received_at) values (%s,%s,%s,%s)",
                                [self.clinic["id"], self.patient["id"], text, self.now])


def _uuid(s) -> UUID | None:
    try:
        return UUID(str(s))
    except (ValueError, TypeError):
        return None


def parse_action(ev) -> tuple[str | None, str | None]:
    """-> (verb, argument). Handles `VERB:arg` payloads and the button-text fallback."""
    p = (ev.payload or "").strip()
    if p:
        verb, _, arg = p.partition(":")
        return verb.upper(), arg
    return TEXT_TO_VERB.get((ev.text or "").strip().casefold()), None


async def load_owned(ctx: Ctx, appt_id: UUID | None, *, lock: bool = True) -> dict | None:
    """The appointment, only if it belongs to the sender; otherwise None (and nothing about it is revealed)."""
    if appt_id is None:
        return None
    return await ctx.one("select * from appointment where id=%s and patient_id=%s" + (" for update" if lock else ""),
                         [appt_id, ctx.patient["id"]])


def is_active(appt: dict | None, now: datetime) -> bool:
    return bool(appt) and appt["status"] in ACTIVE and appt["start_at"] > now


async def soonest_active(ctx: Ctx) -> dict | None:
    return await ctx.one(
        """select * from appointment where patient_id=%s and status = any(%s) and start_at > %s
           order by start_at limit 1 for update""", [ctx.patient["id"], list(ACTIVE), ctx.now])


async def dispatch(conn, patient: dict, ev, messaging=None, now: datetime | None = None) -> str:
    if messaging is None:
        return "recorded"
    now = now or utcnow()
    clinic = await (await conn.execute("select * from clinic where id=%s", [patient["clinic_id"]])).fetchone()
    ctx = Ctx(conn, messaging, clinic, patient, now)
    # re-read: the dict passed in may predate this transaction's own earlier changes
    ctx.patient = await ctx.one("select * from patient where id=%s", [patient["id"]])

    if ev.kind == "text":
        return await handle_text(ctx, ev.text or "")

    verb, arg = parse_action(ev)
    if verb in ("CONFIRM", "RESCHEDULE", "CANCEL"):
        appt = await (soonest_active(ctx) if arg is None else load_owned(ctx, _uuid(arg)))
        if appt is None:
            log.info("%s tap for unknown/foreign appointment %r", verb, arg)
            await ctx.reply(INACTIVE_TEXT)
            return "inactive"
        return await {"CONFIRM": do_confirm, "CANCEL": do_cancel, "RESCHEDULE": do_reschedule}[verb](ctx, appt)
    if verb == "RS":
        return await do_pick(ctx, arg, ev.payload)
    if verb == "RS_NONE":
        return await do_none_of_these(ctx, _uuid(arg))
    if verb == "RECALL_BOOK":
        await ctx.inbox("Replied 'Book now' to the check-up reminder: please call to arrange a visit.")
        await ack(ctx)
        return "recall_book"
    if verb == "RECALL_SKIP":
        await ctx.reply("No problem. Reply here any time you would like to book.")
        return "recall_skip"
    if verb in ("WL_BOOK", "WL_SKIP"):
        outcome, text = await waitlist.respond_to_offer(conn, messaging, clinic, ctx.patient, _uuid(arg),
                                                        verb == "WL_BOOK", now)
        if text:
            await ctx.reply(text)
        return "waitlist_" + outcome
    log.info("unhandled inbound payload %r / %r", ev.payload, ev.text)
    return "ignored"


# ───────────────────────── buttons ─────────────────────────
async def do_confirm(ctx: Ctx, appt: dict) -> str:
    if not is_active(appt, ctx.now):
        await ctx.reply(INACTIVE_TEXT, appt["id"])
        return "inactive"
    if appt["status"] == "confirmed":
        return "already_confirmed"                 # repeated tap: no state change, no second message
    await svc.set_status(ctx.conn, clinic_id=appt["clinic_id"], appt_id=appt["id"], to_status="confirmed",
                         actor="patient", now=ctx.now)
    await ctx.reply(f"Confirmed ✅ See you on {fmt_date(appt['start_at'])} at {fmt_time(appt['start_at'])}.", appt["id"])
    return "confirmed"


async def do_cancel(ctx: Ctx, appt: dict) -> str:
    if not is_active(appt, ctx.now):
        await ctx.reply(INACTIVE_TEXT, appt["id"])
        return "inactive"
    await svc.set_status(ctx.conn, clinic_id=appt["clinic_id"], appt_id=appt["id"], to_status="cancelled",
                         actor="patient", now=ctx.now)
    await ctx.reply(CANCELLED_TEXT, appt["id"])
    await waitlist.offer_freed_slot(ctx.conn, ctx.messaging, ctx.clinic, appt["doctor_id"], appt["start_at"],
                                    appt["duration_min"], ctx.now)
    return "cancelled"


async def send_slot_list(ctx: Ctx, appt: dict, lead_text: str | None = None) -> str:
    options = await reschedule.find_slots(ctx.conn, appt, ctx.now)
    if not options:
        await ctx.reply(NO_SLOTS_TEXT, appt["id"])
        await ctx.inbox(f"No free reschedule slots in the next 7 days for the appointment on "
                        f"{fmt_date(appt['start_at'])} at {fmt_time(appt['start_at'])}.")
        return "no_slots"
    if lead_text:                                   # e.g. the "just taken" apology goes first, as its own message
        await ctx.reply(lead_text, appt["id"])
    await ctx.messaging.send(
        ctx.conn, ctx.clinic,
        Outgoing("interactive_list", reschedule.BODY, ctx.patient["language"], list=reschedule.build_list(appt["id"], options),
                 appointment_id=appt["id"]),
        patient=ctx.patient, proactive=False, now=ctx.now)
    return "list_sent"


async def do_reschedule(ctx: Ctx, appt: dict) -> str:
    if not is_active(appt, ctx.now):
        await ctx.reply(INACTIVE_TEXT, appt["id"])
        return "inactive"
    return "reschedule_" + await send_slot_list(ctx, appt)


# ───────────────────────── list pick ─────────────────────────
async def do_pick(ctx: Ctx, arg: str | None, payload: str | None) -> str:
    parsed = reschedule.parse_row_id(payload or "")
    if not parsed:
        log.info("malformed reschedule row id %r", payload)
        return "ignored"
    appt_id, doctor_id, start_at = parsed
    appt = await load_owned(ctx, appt_id)
    if not is_active(appt, ctx.now):
        await ctx.reply(INACTIVE_TEXT, appt_id if appt else None)
        return "inactive"
    if start_at < ctx.now + reschedule.MIN_LEAD:
        return "stale_" + await send_slot_list(ctx, appt, STALE_TEXT)
    if start_at == appt["start_at"] and doctor_id == appt["doctor_id"]:
        await ctx.reply(f"That is already your appointment time: {fmt_date(start_at)} at {fmt_time(start_at)}.", appt_id)
        return "unchanged"
    old = (appt["doctor_id"], appt["start_at"], appt["duration_min"])
    try:   # one transaction; the database exclusion constraint is the final judge
        await svc.move_appointment(ctx.conn, clinic_id=appt["clinic_id"], appt_id=appt_id, start_at=start_at,
                                   doctor_id=doctor_id, actor="patient", now=ctx.now)
    except (svc.Conflict, svc.Invalid, svc.NotFound) as e:
        log.info("reschedule pick rejected: %s", e.message)
        return "taken_" + await send_slot_list(ctx, appt, TAKEN_TEXT)
    # a fresh confirmation, in the booking-confirmation wording: run the new confirmation job right now
    job = await ctx.one("""select * from reminder_job where appointment_id=%s and type='confirmation' and status='scheduled'
                           order by created_at desc limit 1 for update""", [appt_id])
    if job:
        outcome = await jobs.execute_job(ctx.conn, ctx.messaging, job, now=ctx.now, force=True)
        await jobs._finish(ctx.conn, job, outcome, ctx.now)
    old_doctor, old_start, old_duration = old
    await waitlist.offer_freed_slot(ctx.conn, ctx.messaging, ctx.clinic, old_doctor, old_start, old_duration, ctx.now)
    return "rescheduled"


async def do_none_of_these(ctx: Ctx, appt_id: UUID | None) -> str:
    appt = await load_owned(ctx, appt_id, lock=False)
    when = f" for the appointment on {fmt_date(appt['start_at'])} at {fmt_time(appt['start_at'])}" if appt else ""
    await ctx.inbox(f"None of the offered reschedule times work{when}. Please contact the patient.")
    await ack(ctx)
    return "none_of_these"


# ───────────────────────── free text ─────────────────────────
async def ack(ctx: Ctx) -> bool:
    """Auto-acknowledge at most once per 12 hours per patient."""
    recent = await ctx.one(
        "select 1 as x from outbound_message where patient_id=%s and kind='text' and rendered_body=%s and created_at > %s",
        [ctx.patient["id"], ACK_TEXT, ctx.now - ACK_EVERY])
    if recent:
        return False
    return (await ctx.reply(ACK_TEXT)).outcome == "sent"


async def handle_text(ctx: Ctx, text: str) -> str:
    if optout.is_stop_request(text):
        if ctx.patient["opted_out"]:
            return "already_opted_out"             # repeated STOP: nothing to change, nothing more to send
        await ctx.conn.execute("update patient set opted_out=true, opted_out_at=%s where id=%s", [ctx.now, ctx.patient["id"]])
        await ctx.reply(f"You will not receive further WhatsApp updates from {ctx.clinic['name']}.", bypass_opt_out=True)
        return "opted_out"
    await ctx.inbox(text)
    await ack(ctx)                                  # silently skipped for opted-out patients by the send guard
    return "inbox"
