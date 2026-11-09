from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import Staff, current_staff
from ..db import get_conn
from ..messaging.service import Outgoing
from ..timeutil import utcnow

router = APIRouter(prefix="/inbox", tags=["inbox"])

LIST_SQL = """
select i.id, i.patient_id, p.name as patient_name, p.phone as patient_phone, p.opted_out as patient_opted_out,
       i.text, i.received_at, i.handled, i.handled_by,
       exists (select 1 from message_event m where m.event in ('button','list_reply','text')
               and m.payload->>'from' = p.phone and m.created_at > now() - interval '24 hours') as window_open
from inbox_item i join patient p on p.id = i.patient_id
"""
WINDOW_CLOSED = ("The 24-hour WhatsApp window is closed: this patient has not messaged in the last 24 hours, so only "
                 "approved templates can be sent. Phone them instead.")


@router.get("")
async def list_items(status: Literal["open", "handled", "all"] = "open", limit: int = Query(100, le=200),
                     staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    q = LIST_SQL + " where i.clinic_id=%s"
    if status == "open":
        q += " and not i.handled"
    elif status == "handled":
        q += " and i.handled"
    cur = await conn.execute(q + " order by i.handled, i.received_at desc limit %s", [staff.clinic_id, limit])
    return await cur.fetchall()


@router.get("/count")
async def open_count(staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    row = await (await conn.execute("select count(*) as n from inbox_item where clinic_id=%s and not handled",
                                    [staff.clinic_id])).fetchone()
    return {"open": row["n"]}


class Handled(BaseModel):
    handled: bool = True


async def _load(conn, staff: Staff, item_id: UUID) -> dict:
    row = await (await conn.execute(LIST_SQL + " where i.id=%s and i.clinic_id=%s", [item_id, staff.clinic_id])).fetchone()
    if not row:
        raise HTTPException(404, {"code": "inbox_item_not_found", "message": "Inbox item not found"})
    return row


@router.post("/{item_id}/handled")
async def set_handled(item_id: UUID, body: Handled | None = None, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    handled = True if body is None else body.handled
    await _load(conn, staff, item_id)
    await conn.execute("update inbox_item set handled=%s, handled_by=%s where id=%s",
                       [handled, staff.user_id if handled else None, item_id])
    return await _load(conn, staff, item_id)


class Reply(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/{item_id}/reply")
async def reply(item_id: UUID, body: Reply, request: Request, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    """A free-form session message: only while the patient's 24-hour window is open. Goes through every normal guard
    (opt-out, medical content), and marks the item handled when sent."""
    item = await _load(conn, staff, item_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(422, {"code": "empty", "message": "Type a message first"})
    if not item["window_open"]:
        raise HTTPException(409, {"code": "window_closed", "message": WINDOW_CLOSED})
    clinic = await (await conn.execute("select * from clinic where id=%s", [staff.clinic_id])).fetchone()
    patient = await (await conn.execute("select * from patient where id=%s", [item["patient_id"]])).fetchone()
    res = await request.app.state.messaging.send(conn, clinic, Outgoing("text", text, patient["language"]),
                                                 patient=patient, proactive=False, now=utcnow())
    if res.outcome == "blocked":
        raise HTTPException(422, {"code": "medical_content", "message": "Replies cannot mention treatments or medical details."})
    if res.outcome == "skipped":
        msg = "This patient has opted out of WhatsApp updates." if res.reason == "opted_out" else WINDOW_CLOSED
        raise HTTPException(409, {"code": res.reason, "message": msg})
    if res.outcome == "failed":
        raise HTTPException(502, {"code": res.reason or "send_failed", "message": "The message could not be sent."})
    await conn.execute("update inbox_item set handled=true, handled_by=%s where id=%s", [staff.user_id, item_id])
    return {"sent": True, "outbound_message_id": res.outbound_id}
