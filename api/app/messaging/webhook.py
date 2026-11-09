"""One handler for every inbound WhatsApp event, whether it came from Meta's webhook or the Simulator.
Dedupes on message / status id, so redelivering the same payload changes state exactly once."""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from typing import Any

from psycopg.types.json import Jsonb

log = logging.getLogger("showup.webhook")

STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3}


@dataclass
class StatusEvent:
    wamid: str
    status: str                      # sent | delivered | read | failed
    errors: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"status:{self.wamid}:{self.status}"


@dataclass
class InboundEvent:
    msg_id: str
    from_phone: str                  # E.164 with '+'
    kind: str                        # button | list_reply | button_reply | text
    payload: str | None = None       # button payload / list row id / button id
    text: str | None = None          # visible text: button text, row title or message body
    context_wamid: str | None = None  # the outbound message this replies to
    raw: dict = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"msg:{self.msg_id}"

    @property
    def event_name(self) -> str:      # value for message_event.event
        return {"button": "button", "button_reply": "button", "list_reply": "list_reply", "text": "text"}[self.kind]


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(raw body, app secret). Fails closed without a secret."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def sign(raw_body: bytes, app_secret: str) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def parse_webhook(payload: dict) -> list[StatusEvent | InboundEvent]:
    events: list[StatusEvent | InboundEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for st in value.get("statuses", []):
                if st.get("id") and st.get("status"):
                    events.append(StatusEvent(st["id"], st["status"], st.get("errors", []), st))
            for m in value.get("messages", []):
                ev = _parse_message(m)
                if ev:
                    events.append(ev)
                else:
                    log.info("ignoring unsupported inbound message type %r", m.get("type"))
    return events


def _parse_message(m: dict) -> InboundEvent | None:
    frm = "+" + str(m.get("from", "")).lstrip("+")
    ctx = (m.get("context") or {}).get("id")
    base = dict(msg_id=m.get("id"), from_phone=frm, context_wamid=ctx, raw=m)
    t = m.get("type")
    if not base["msg_id"]:
        return None
    if t == "button":                                   # template quick-reply tap
        b = m.get("button", {})
        return InboundEvent(kind="button", payload=b.get("payload"), text=b.get("text"), **base)
    if t == "interactive":
        i = m.get("interactive", {})
        if i.get("type") == "list_reply":
            r = i.get("list_reply", {})
            return InboundEvent(kind="list_reply", payload=r.get("id"), text=r.get("title"), **base)
        if i.get("type") == "button_reply":
            r = i.get("button_reply", {})
            return InboundEvent(kind="button_reply", payload=r.get("id"), text=r.get("title"), **base)
        return None
    if t == "text":
        return InboundEvent(kind="text", text=(m.get("text") or {}).get("body", ""), **base)
    return None


async def _one(conn, sql, args):
    return await (await conn.execute(sql, args)).fetchone()


async def _apply_status(conn, ev: StatusEvent) -> None:
    row = await _one(conn, "select id, status from outbound_message where wa_message_id=%s for update", [ev.wamid])
    if not row:
        return
    if ev.status == "failed":
        err = "; ".join(f"{e.get('code')}: {e.get('title') or e.get('message')}" for e in ev.errors) or "failed"
        await conn.execute("update outbound_message set status='failed', error=%s where id=%s", [err, row["id"]])
    elif STATUS_RANK.get(ev.status, 0) > STATUS_RANK.get(row["status"], 0):  # never move backwards
        await conn.execute("update outbound_message set status=%s where id=%s", [ev.status, row["id"]])


async def process_event(conn, ev: StatusEvent | InboundEvent, messaging=None, now=None) -> dict:
    """Process one event in the caller's transaction. Returns a small summary dict."""
    if isinstance(ev, StatusEvent):
        ob = await _one(conn, "select clinic_id from outbound_message where wa_message_id=%s", [ev.wamid])
        if not ob:
            return {"type": "status", "result": "unknown_message", "id": ev.wamid}
        clinic_id, payload = ob["clinic_id"], {"errors": ev.errors}
    else:
        patient = await _one(conn, "select * from patient where phone=%s order by created_at limit 1", [ev.from_phone])
        if not patient:
            return {"type": ev.kind, "result": "unknown_sender", "id": ev.msg_id}
        clinic_id = patient["clinic_id"]
        payload = {"from": ev.from_phone, "kind": ev.kind, "payload": ev.payload, "text": ev.text,
                   "context_wamid": ev.context_wamid, "patient_id": str(patient["id"])}
    # Dedupe first: the unique key makes a concurrent duplicate wait for us, then do nothing.
    ins = await _one(
        conn,
        """insert into message_event (clinic_id, wa_message_id, event, payload, dedupe_key) values (%s,%s,%s,%s,%s)
           on conflict (dedupe_key) do nothing returning id""",
        [clinic_id, ev.wamid if isinstance(ev, StatusEvent) else ev.msg_id,
         ev.status if isinstance(ev, StatusEvent) else ev.event_name, Jsonb(payload), ev.dedupe_key])
    if not ins:
        return {"type": "status" if isinstance(ev, StatusEvent) else ev.kind, "result": "duplicate",
                "id": ev.dedupe_key}
    if isinstance(ev, StatusEvent):
        await _apply_status(conn, ev)
        return {"type": "status", "result": "applied", "status": ev.status, "id": ev.wamid}
    from . import inbound  # late import: Phase 3 logic lives there
    outcome = await inbound.dispatch(conn, patient, ev, messaging, now)
    return {"type": ev.kind, "result": "processed", "id": ev.msg_id, "outcome": outcome}


async def handle_payload(pool, payload: dict, messaging=None, now=None) -> list[dict]:
    """Entry point shared by /webhooks/whatsapp, /simulator/inbound and the Simulator's status emitter.
    Each event gets its own transaction, so one bad event cannot lose the rest (or leave a dedupe row behind)."""
    results = []
    for ev in parse_webhook(payload):
        try:
            async with pool.connection() as conn:
                results.append(await process_event(conn, ev, messaging, now))
        except Exception:
            log.exception("failed processing %s", ev)
            results.append({"result": "error", "id": getattr(ev, "dedupe_key", None)})
    return results
