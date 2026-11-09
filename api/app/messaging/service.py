"""MessagingService.send: guards -> persist outbound_message -> Transport.deliver. Live and Simulator share all of it."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from ..timeutil import utcnow
from . import guards, meta_payloads
from .transport import Delivery, LiveTransport, SimulatorTransport, TransportError
from .webhook import handle_payload

log = logging.getLogger("showup.messaging")
SESSION_WINDOW = timedelta(hours=24)
_bg_tasks: set[asyncio.Task] = set()


@dataclass
class Outgoing:
    kind: Literal["template", "interactive_list", "interactive_buttons", "text"]
    body: str
    language: str = "en"
    template_name: str | None = None
    footer: str | None = None
    buttons: list | None = None
    list: dict | None = None
    appointment_id: UUID | None = None
    params: list[str] | None = None        # template variable values, in order

    @classmethod
    def from_rendered(cls, r, *, appointment_id: UUID | None = None) -> "Outgoing":
        return cls("template", r.body, r.language, r.template_name, r.footer, r.buttons or None, None, appointment_id, r.params)


@dataclass
class SendResult:
    outcome: Literal["sent", "skipped", "blocked", "failed"]
    reason: str | None = None
    outbound_id: UUID | None = None
    transient: bool = False


async def _one(conn, sql, args):
    return await (await conn.execute(sql, args)).fetchone()


class MessagingService:
    def __init__(self, pool, *, sim_delays: tuple[float, float] | None = (1.0, 3.0), live=None):
        """sim_delays = (delivered_after_s, read_after_s) for Simulator status events; None disables them (tests)."""
        self.pool, self.sim_delays = pool, sim_delays
        self.transports = {"simulator": SimulatorTransport(), "live": live or LiveTransport()}

    async def window_open(self, conn, phone: str, now: datetime) -> bool:
        """Has this person messaged us in the last 24 hours? Free-form messages are only allowed then."""
        row = await _one(
            conn, """select 1 as x from message_event where event in ('button','list_reply','text')
                     and payload->>'from' = %s and created_at > %s limit 1""", [phone, now - SESSION_WINDOW])
        return bool(row)

    async def send(self, conn, clinic: dict, out: Outgoing, *, patient: dict | None = None, doctor: dict | None = None,
                   proactive: bool = True, ignore_quiet_hours: bool = False, now: datetime | None = None,
                   bypass_opt_out: bool = False) -> SendResult:
        """`bypass_opt_out` exists for exactly one message: the final "you will not receive further updates"
        reply to the patient's own STOP. Nothing else may use it."""
        assert (patient is None) != (doctor is None), "exactly one recipient"
        now = now or utcnow()
        who = patient or doctor
        phone = patient["phone"] if patient else (doctor.get("whatsapp_phone") or "")

        # ── guards (order matters: cheapest/most absolute first) ──
        if patient and patient["opted_out"] and not bypass_opt_out:
            return SendResult("skipped", "opted_out")
        names = [r["name"] for r in await (await conn.execute(
            "select name from treatment_type where clinic_id=%s", [clinic["id"]])).fetchall()]
        text = " ".join(filter(None, [out.body, out.footer, *[b.get("text", "") for b in (out.buttons or [])]]))
        if out.list:
            text += " " + " ".join([out.list.get("header", ""), out.list.get("button", "")] + [
                f"{r.get('title', '')} {r.get('description', '')}"
                for s in out.list.get("sections", []) for r in s.get("rows", [])])
        hits = guards.find_medical_terms(text, names)
        if hits:
            log.error("MEDICAL CONTENT BLOCKED: template=%s terms=%s body=%r", out.template_name, hits, out.body)
            return SendResult("blocked", "medical_content")
        if not phone:
            return SendResult("failed", "no_phone")
        if out.kind != "template" and not await self.window_open(conn, phone, now):
            return SendResult("skipped", "window_closed")
        if proactive and not ignore_quiet_hours and guards.is_quiet(
                now.astimezone(guards.IST).time(), clinic["quiet_hours_start"], clinic["quiet_hours_end"]):
            return SendResult("skipped", "quiet_hours")

        # ── persist, then deliver ──
        transport_name = guards.choose_transport(clinic["messaging_mode"], is_demo_seed=bool(who.get("is_demo_seed")))
        row = await _one(
            conn,
            """insert into outbound_message (clinic_id, patient_id, doctor_id, appointment_id, kind, template_name,
                   language, rendered_body, footer, buttons, list, transport, status)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued') returning id""",
            [clinic["id"], patient["id"] if patient else None, doctor["id"] if doctor else None, out.appointment_id,
             out.kind, out.template_name, out.language, out.body, out.footer,
             Jsonb(out.buttons) if out.buttons is not None else None,
             Jsonb(out.list) if out.list is not None else None, transport_name])
        d = Delivery(row["id"], phone, out.kind, out.body, out.template_name, out.language, out.footer,
                     out.buttons, out.list, bool(who.get("is_demo_seed")), out.params)
        try:
            wamid = await self.transports[transport_name].deliver(d)
        except TransportError as e:
            await conn.execute("update outbound_message set status='failed', error=%s where id=%s",
                               [f"{e.code or 'error'}: {e}", row["id"]])
            return SendResult("failed", f"{e.code or 'transport_error'}", row["id"], e.transient)
        await conn.execute("update outbound_message set status='sent', wa_message_id=%s where id=%s",
                           [wamid, row["id"]])
        await conn.execute(
            """insert into message_event (clinic_id, wa_message_id, event, payload, dedupe_key)
               values (%s,%s,'sent','{}',%s) on conflict (dedupe_key) do nothing""",
            [clinic["id"], wamid, f"status:{wamid}:sent"])
        if transport_name == "simulator" and self.sim_delays:
            self._schedule_sim_followups(wamid, phone)
        return SendResult("sent", None, row["id"])

    # ── Simulator status progression: sent (inline) -> delivered (~1s) -> read (~3s, or sooner if the chat is open) ──
    def _schedule_sim_followups(self, wamid: str, phone: str) -> None:
        async def run():
            delivered_after, read_after = self.sim_delays
            await asyncio.sleep(delivered_after)  # also lets the sending transaction commit
            await self.emit_status(wamid, "delivered", phone)
            await asyncio.sleep(max(0.0, read_after - delivered_after))
            await self.emit_status(wamid, "read", phone)
        t = asyncio.get_running_loop().create_task(run())
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)

    async def emit_status(self, wamid: str, status: str, phone: str) -> None:
        """Simulator statuses go through the very same handler that processes Meta's status webhooks."""
        try:
            await handle_payload(self.pool, meta_payloads.status_payload(wamid, status, phone), self)
        except Exception:
            log.exception("simulator status emit failed")
