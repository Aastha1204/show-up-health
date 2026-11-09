"""Backend for the in-browser WhatsApp Simulator. Taps arrive on /simulator/inbound as Meta-shaped payloads and run
through exactly the same handler as the real webhook; only the signature check is skipped."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..auth import Staff, current_staff
from ..config import get_settings
from ..db import get_conn
from ..messaging import webhook

router = APIRouter(prefix="/simulator", tags=["simulator"])


async def demo_staff(staff: Staff = Depends(current_staff)) -> Staff:
    if not get_settings().demo_mode:
        raise HTTPException(404, "Not found")
    return staff


@router.post("/inbound")
async def inbound(request: Request, payload: dict = Body(...), staff: Staff = Depends(demo_staff)):
    results = await webhook.handle_payload(request.app.state.pool, payload, request.app.state.messaging)
    return {"results": results}


@router.get("/threads")
async def threads(staff: Staff = Depends(demo_staff), conn=Depends(get_conn)):
    """Chats the presenter can switch between: every patient/doctor we have messaged, newest first."""
    pts = await (await conn.execute(
        """select 'patient' as kind, p.id, p.name, p.phone, p.is_demo_seed, max(m.created_at) as last_at,
                  (array_agg(m.rendered_body order by m.created_at desc))[1] as last_body
           from outbound_message m join patient p on p.id = m.patient_id
           where m.clinic_id=%s and m.transport='simulator' group by p.id""", [staff.clinic_id])).fetchall()
    docs = await (await conn.execute(
        """select 'doctor' as kind, d.id, d.name, d.whatsapp_phone as phone, d.is_demo_seed, max(m.created_at) as last_at,
                  (array_agg(m.rendered_body order by m.created_at desc))[1] as last_body
           from outbound_message m join doctor d on d.id = m.doctor_id
           where m.clinic_id=%s and m.transport='simulator' group by d.id""", [staff.clinic_id])).fetchall()
    return sorted([*pts, *docs], key=lambda r: r["last_at"], reverse=True)[:40]


async def _recipient(conn, clinic_id, kind: str, rid: UUID) -> dict:
    table, phone_col = ("patient", "phone") if kind == "patient" else ("doctor", "whatsapp_phone")
    row = await (await conn.execute(f"select id, name, {phone_col} as phone from {table} where id=%s and clinic_id=%s",
                                    [rid, clinic_id])).fetchone()
    if not row:
        raise HTTPException(404, "No such chat")
    return row


@router.get("/threads/{kind}/{rid}/messages")
async def messages(kind: Literal["patient", "doctor"], rid: UUID, staff: Staff = Depends(demo_staff),
                   conn=Depends(get_conn)):
    who = await _recipient(conn, staff.clinic_id, kind, rid)
    col = "patient_id" if kind == "patient" else "doctor_id"
    out = await (await conn.execute(
        f"""select id, kind, template_name, language, rendered_body as body, footer, buttons, list, transport,
                   wa_message_id, status, error, appointment_id, created_at
            from outbound_message where {col}=%s order by created_at""", [rid])).fetchall()
    inc = await (await conn.execute(
        """select id, event, payload, created_at from message_event
           where event in ('button','list_reply','text') and payload->>'from' = %s order by created_at""",
        [who["phone"]])).fetchall()
    answered = {e["payload"].get("context_wamid") for e in inc if e["payload"].get("context_wamid")}
    items = [{**m, "direction": "out", "consumed": m["wa_message_id"] in answered} for m in out]
    items += [{"id": e["id"], "direction": "in", "kind": e["event"], "body": e["payload"].get("text") or "",
               "payload": e["payload"].get("payload"), "created_at": e["created_at"]} for e in inc]
    items.sort(key=lambda m: m["created_at"])
    return {"recipient": who, "messages": items}


@router.post("/threads/{kind}/{rid}/read")
async def mark_read(kind: Literal["patient", "doctor"], rid: UUID, request: Request,
                    staff: Staff = Depends(demo_staff), conn=Depends(get_conn)):
    """The presenter opened the chat: every simulator message in it turns read (blue ticks)."""
    who = await _recipient(conn, staff.clinic_id, kind, rid)
    col = "patient_id" if kind == "patient" else "doctor_id"
    rows = await (await conn.execute(
        f"""select wa_message_id from outbound_message where {col}=%s and transport='simulator'
            and status in ('sent','delivered') and wa_message_id is not null""", [rid])).fetchall()
    msgr = request.app.state.messaging
    for r in rows:
        await msgr.emit_status(r["wa_message_id"], "delivered", who["phone"])
        await msgr.emit_status(r["wa_message_id"], "read", who["phone"])
    return {"marked": len(rows)}
