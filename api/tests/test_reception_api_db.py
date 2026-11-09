"""API behind the reception UI: staff cancel/move free slots for the waitlist, call list, message log, inbox, paging."""
import uuid
from datetime import timedelta

import pytest

from app.messaging import meta_payloads as mp, webhook
from app.services import appointments as svc
from app.timeutil import utcnow
from .conftest import at, token_for
from .test_messaging_db import book, q

pytestmark = pytest.mark.db

P1 = "+919876500000"


def soon(hours: float):
    """A future-ish start on the 15-minute grid, `hours` from now."""
    t = utcnow() + timedelta(hours=hours)
    return t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)


async def raw_appt(pool, w, patient, start, *, doctor="neha", status="pending"):
    async with pool.connection() as conn:
        return await svc.create_appointment(conn, clinic_id=w["clinic"], patient_id=w[patient], doctor_id=w[doctor],
                                            treatment_type_id=w["consult"], start_at=start, enforce_hours=False,
                                            schedule_reminders=False, status=status)


async def waiting_patient(pool, w, n=1):
    pid = uuid.uuid4()
    await q(pool, "insert into patient (id, clinic_id, name, phone, consent_source) values (%s,%s,%s,%s,'reception') returning id",
            [pid, w["clinic"], f"Waiter {n}", f"+91987653{n:04d}"])
    await q(pool, """insert into waitlist_entry (clinic_id, patient_id, doctor_id, treatment_type_id, preferred_date, preferred_window)
                     values (%s,%s,%s,%s,%s,'morning') returning id""", [w["clinic"], pid, w["neha"], w["consult"], w["day"]])
    return pid


# ───────────────────────── list rows carry the last event (drives toasts) ─────────────────────────
async def test_list_rows_expose_the_last_status_event(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    row = (await client.get("/appointments", headers=h, params={"date": world["day"].isoformat()})).json()[0]
    assert (row["last_event_status"], row["last_event_actor"]) == ("pending", "reception")
    await webhook.handle_payload(pool, mp.button_payload(P1, f"CONFIRM:{a['id']}", "Confirm"), messaging)
    row = (await client.get("/appointments", headers=h, params={"date": world["day"].isoformat()})).json()[0]
    assert (row["status"], row["last_event_status"], row["last_event_actor"]) == ("confirmed", "confirmed", "patient")
    assert row["last_event_at"] and row["patient_is_demo_seed"] is False


# ───────────────────────── call list ─────────────────────────
async def test_call_list_is_pending_appointments_in_the_next_12_hours(client, pool, world):
    h = token_for(world["recep_user"])
    await raw_appt(pool, world, "p1", soon(4))                                   # yes
    await raw_appt(pool, world, "p2", soon(3), doctor="arjun")                   # yes, sooner
    await raw_appt(pool, world, "p3", soon(13))                                  # too far
    await raw_appt(pool, world, "p1", soon(2), doctor="arjun", status="confirmed")   # already confirmed
    await raw_appt(pool, world, "p2", utcnow() - timedelta(hours=1), doctor="neha", status="pending")  # already started
    rows = (await client.get("/appointments/call-list", headers=h)).json()
    assert [r["patient_name"] for r in rows] == ["Patient 2", "Patient 1"]      # soonest first
    assert all(r["status"] == "pending" and r["patient_phone"].startswith("+91") for r in rows)
    wide = (await client.get("/appointments/call-list", headers=h, params={"hours": 24})).json()
    assert len(wide) == 3


async def test_called_confirmed_is_recorded_as_a_phone_confirmation(client, pool, world):
    h = token_for(world["recep_user"])
    a = await raw_appt(pool, world, "p1", soon(4))
    r = await client.post(f"/appointments/{a['id']}/status", headers=h, json={"status": "confirmed", "via": "phone"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    events = (await client.get(f"/appointments/{a['id']}", headers=h)).json()["events"]
    assert (events[-1]["to_status"], events[-1]["actor"], events[-1]["detail"]) == ("confirmed", "reception", {"via": "phone"})
    assert (await client.get("/appointments/call-list", headers=h)).json() == []   # drops off the call list


# ───────────────────────── staff cancel / move free the slot for the waitlist ─────────────────────────
async def test_receptionist_cancelling_offers_the_slot_to_the_waitlist(client, pool, world, clinic_open_24h):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    waiter = await waiting_patient(pool, world)
    r = await client.post(f"/appointments/{a['id']}/status", headers=h, json={"status": "cancelled"})
    assert r.status_code == 200
    (offer,) = await q(pool, "select * from waitlist_offer where status='pending'")
    assert (offer["doctor_id"], offer["slot_start_at"]) == (world["neha"], a["start_at"])
    assert [m["patient_id"] for m in await q(pool, "select patient_id from outbound_message where template_name='showup_waitlist_offer'")] == [waiter]
    again = await client.post(f"/appointments/{a['id']}/status", headers=h, json={"status": "cancelled"})
    assert again.status_code == 200 and len(await q(pool, "select 1 from waitlist_offer")) == 1   # no second offer


async def test_other_status_changes_do_not_trigger_offers(client, pool, world, clinic_open_24h):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    await waiting_patient(pool, world)
    for st in ("confirmed", "arrived"):
        assert (await client.post(f"/appointments/{a['id']}/status", headers=h, json={"status": st})).status_code == 200
    assert await q(pool, "select 1 from waitlist_offer") == []


async def test_receptionist_moving_an_appointment_offers_the_old_slot(client, pool, world, clinic_open_24h):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    waiter = await waiting_patient(pool, world)
    r = await client.patch(f"/appointments/{a['id']}", headers=h, json={"start_at": at(world["day"], "17:30").isoformat()})
    assert r.status_code == 200 and r.json()["status"] == "rescheduled"
    (offer,) = await q(pool, "select * from waitlist_offer where status='pending'")
    assert offer["slot_start_at"] == a["start_at"]
    assert (await q(pool, "select patient_id from outbound_message where template_name='showup_waitlist_offer'"))[0]["patient_id"] == waiter


async def test_editing_a_note_does_not_offer_anything(client, pool, world, clinic_open_24h):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    await waiting_patient(pool, world)
    assert (await client.patch(f"/appointments/{a['id']}", headers=h, json={"internal_note": "bring x-rays"})).status_code == 200
    assert await q(pool, "select 1 from waitlist_offer") == []


# ───────────────────────── message log ─────────────────────────
async def test_message_log_merges_messages_ticks_replies_and_status_changes(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    fired = (await client.post("/demo/fire", headers=h, json={"appointment_id": str(a["id"]), "type": "day_before"})).json()
    assert fired["status"] == "sent"
    wamid = (await q(pool, "select wa_message_id from outbound_message"))[0]["wa_message_id"]
    for st in ("delivered", "read"):
        await messaging.emit_status(wamid, st, P1)
    await webhook.handle_payload(pool, mp.button_payload(P1, f"RESCHEDULE:{a['id']}", "Reschedule", context_wamid=wamid), messaging)
    await client.post(f"/appointments/{a['id']}/status", headers=h, json={"status": "confirmed", "via": "phone"})
    log = (await client.get(f"/appointments/{a['id']}/log", headers=h)).json()
    kinds = [i["type"] for i in log]
    assert kinds[0] == "status" and "reply" in kinds and kinds[-1] == "status"
    assert [i["at"] for i in log] == sorted(i["at"] for i in log)                # one chronological timeline
    msg = next(i for i in log if i["type"] == "message" and i["template_name"] == "showup_day_before")
    assert msg["transport"] == "simulator" and msg["status"] == "read"
    assert set(msg["ticks"]) == {"sent", "delivered", "read"}                     # the sent -> delivered -> read story
    assert msg["ticks"]["sent"] <= msg["ticks"]["delivered"] <= msg["ticks"]["read"]
    reply = next(i for i in log if i["type"] == "reply")
    assert (reply["via"], reply["text"]) == ("button", "Reschedule")
    assert any(i["type"] == "message" and i["kind"] == "interactive_list" for i in log)   # the slot list that tap triggered
    assert [(i["to_status"], i["actor"]) for i in log if i["type"] == "status"] == [("pending", "reception"), ("confirmed", "reception")]


async def test_message_log_is_scoped_and_404s_for_unknown(client, pool, world):
    h = token_for(world["recep_user"])
    assert (await client.get(f"/appointments/{uuid.uuid4()}/log", headers=h)).status_code == 404
    assert (await client.get(f"/appointments/{uuid.uuid4()}/log")).status_code == 401


# ───────────────────────── inbox ─────────────────────────
async def say(pool, messaging, text, phone=P1):
    await webhook.handle_payload(pool, mp.text_payload(phone, text), messaging)


async def test_inbox_lists_open_items_and_marks_them_handled(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    await say(pool, messaging, "Can I come earlier?")
    items = (await client.get("/inbox", headers=h)).json()
    assert [(i["patient_name"], i["text"], i["handled"], i["window_open"]) for i in items] == [("Patient 1", "Can I come earlier?", False, True)]
    assert (await client.get("/inbox/count", headers=h)).json() == {"open": 1}
    done = (await client.post(f"/inbox/{items[0]['id']}/handled", headers=h, json={"handled": True})).json()
    assert done["handled"] is True and done["handled_by"] == str(world["recep_user"])
    assert (await client.get("/inbox", headers=h)).json() == []
    assert len((await client.get("/inbox", headers=h, params={"status": "handled"})).json()) == 1
    assert (await client.get("/inbox/count", headers=h)).json() == {"open": 0}
    undone = (await client.post(f"/inbox/{items[0]['id']}/handled", headers=h, json={"handled": False})).json()
    assert undone["handled"] is False and undone["handled_by"] is None


async def test_inbox_reply_sends_a_session_message_and_handles_the_item(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    await say(pool, messaging, "Can I come earlier?")
    (item,) = (await client.get("/inbox", headers=h)).json()
    r = await client.post(f"/inbox/{item['id']}/reply", headers=h, json={"text": "Yes, 10:00 works. See you then."})
    assert r.status_code == 200 and r.json()["sent"] is True
    last = (await q(pool, "select kind, rendered_body, transport from outbound_message order by created_at desc limit 1"))[0]
    assert (last["kind"], last["rendered_body"], last["transport"]) == ("text", "Yes, 10:00 works. See you then.", "simulator")
    assert (await client.get("/inbox", headers=h)).json() == []                    # handled by replying


async def test_inbox_reply_is_refused_when_the_24h_window_is_closed(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    await say(pool, messaging, "Hello")
    await q(pool, "update message_event set created_at = now() - interval '25 hours' returning id")
    (item,) = (await client.get("/inbox", headers=h)).json()
    assert item["window_open"] is False
    r = await client.post(f"/inbox/{item['id']}/reply", headers=h, json={"text": "Hi!"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "window_closed" and "24-hour" in r.json()["detail"]["message"]
    assert (await q(pool, "select count(*) as n from outbound_message where rendered_body='Hi!'"))[0]["n"] == 0


async def test_inbox_reply_guards_opt_out_and_medical_content(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    await say(pool, messaging, "Hello")
    (item,) = (await client.get("/inbox", headers=h)).json()
    bad = await client.post(f"/inbox/{item['id']}/reply", headers=h, json={"text": "About your root canal tomorrow"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "medical_content"
    assert (await client.post(f"/inbox/{item['id']}/reply", headers=h, json={"text": "   "})).status_code == 422
    await q(pool, "update patient set opted_out=true returning id")
    out = await client.post(f"/inbox/{item['id']}/reply", headers=h, json={"text": "Hello again"})
    assert out.status_code == 409 and out.json()["detail"]["code"] == "opted_out"
    assert (await client.get("/inbox", headers=h)).json()[0]["handled"] is False  # nothing was sent, so still open


async def test_inbox_unknown_item_and_auth(client, world):
    h = token_for(world["recep_user"])
    assert (await client.post(f"/inbox/{uuid.uuid4()}/handled", headers=h)).status_code == 404
    assert (await client.post(f"/inbox/{uuid.uuid4()}/reply", headers=h, json={"text": "x"})).status_code == 404
    assert (await client.get("/inbox")).status_code == 401


# ───────────────────────── patients paging ─────────────────────────
async def test_patients_list_pages_in_name_order(client, pool, world):
    h = token_for(world["recep_user"])
    for n in range(4):
        await q(pool, "insert into patient (clinic_id, name, phone, consent_source) values (%s,%s,%s,'import') returning id",
                [world["clinic"], f"Zed {n}", f"+91987654{n:04d}"])
    page1 = (await client.get("/patients", headers=h, params={"limit": 3})).json()
    page2 = (await client.get("/patients", headers=h, params={"limit": 3, "offset": 3})).json()
    assert [p["name"] for p in page1] == ["Patient 1", "Patient 2", "Patient 3"]
    assert [p["name"] for p in page2] == ["Zed 0", "Zed 1", "Zed 2"]
    assert {"consent_source", "consent_at", "opted_out", "no_show_count", "language", "is_demo_seed"} <= set(page1[0])
