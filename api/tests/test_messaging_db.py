"""Messaging core against a real database: scheduling rules, the tick, guards, webhook, dedupe, simulator."""
import asyncio
import json
from datetime import time, timedelta
from uuid import UUID

import pytest

from app import jobs
from app.messaging import meta_payloads as mp, webhook
from app.messaging.service import Outgoing
from app.services import appointments as svc
from app.timeutil import IST, ist_to_utc, to_ist
from .conftest import at, token_for

pytestmark = pytest.mark.db


def frozen(w):
    """Noon IST on the Sunday before the test Monday: not quiet hours, deterministic."""
    return ist_to_utc(w["day"] - timedelta(days=1), time(12, 0))


async def q(pool, sql, args=()):
    async with pool.connection() as conn:
        return await (await conn.execute(sql, list(args))).fetchall()


async def book(pool, w, hhmm, *, patient="p1", doctor="neha", tt="consult", now=None, **kw):
    async with pool.connection() as conn:
        return await svc.create_appointment(
            conn, clinic_id=w["clinic"], patient_id=w[patient], doctor_id=w[doctor], treatment_type_id=w[tt],
            start_at=at(w["day"], hhmm), now=now, **kw)


async def job_rows(pool, appt_id):
    return await q(pool, "select type,status,due_at,skip_reason from reminder_job where appointment_id=%s order by due_at, type", [appt_id])


async def clinic_and(pool, w, patient="p1"):
    c = (await q(pool, "select * from clinic where id=%s", [w["clinic"]]))[0]
    p = (await q(pool, "select * from patient where id=%s", [w[patient]]))[0]
    return c, p


def post_signed(client, payload, secret="test-app-secret"):
    raw = json.dumps(payload).encode()
    return client.post("/webhooks/whatsapp", content=raw,
                       headers={"X-Hub-Signature-256": webhook.sign(raw, secret), "Content-Type": "application/json"})


# ───────────────────────── scheduling rules (spec 7.1) ─────────────────────────
async def test_1030_ist_appointment_gets_day_before_at_0500_utc_previous_day(pool, world):
    a = await book(pool, world, "10:30", now=frozen(world) - timedelta(days=2))   # booked days ahead
    rows = {r["type"]: r for r in await job_rows(pool, a["id"])}
    assert set(rows) == {"confirmation", "day_before", "two_hour"}
    db = rows["day_before"]["due_at"]
    assert (db.hour, db.minute, db.utcoffset().total_seconds()) == (5, 0, 0)
    assert db.date() == world["day"] - timedelta(days=1)
    assert to_ist(db).strftime("%H:%M") == "10:30"
    assert to_ist(rows["two_hour"]["due_at"]).strftime("%H:%M") == "08:30"


async def test_booked_inside_24h_but_over_3h_sends_day_before_5_min_after_confirmation(pool, world):
    now = ist_to_utc(world["day"], time(9, 0))               # 8h before a 17:00 visit
    a = await book(pool, world, "17:00", now=now)
    rows = {r["type"]: r for r in await job_rows(pool, a["id"])}
    assert rows["confirmation"]["due_at"] == now
    assert rows["day_before"]["due_at"] == now + timedelta(minutes=5)


async def test_booked_inside_3h_has_no_day_before_and_inside_2h_no_two_hour(pool, world):
    a = await book(pool, world, "17:00", now=ist_to_utc(world["day"], time(14, 30)))
    assert {r["type"] for r in await job_rows(pool, a["id"])} == {"confirmation", "two_hour"}
    b = await book(pool, world, "17:30", patient="p2", now=ist_to_utc(world["day"], time(16, 30)))
    assert {r["type"] for r in await job_rows(pool, b["id"])} == {"confirmation"}


async def test_already_confirmed_appointment_gets_no_day_before(pool, world):
    a = await book(pool, world, "10:30", status="confirmed", now=frozen(world))
    assert "day_before" not in {r["type"] for r in await job_rows(pool, a["id"])}


async def test_night_time_confirmation_waits_for_8am(pool, world):
    night = ist_to_utc(world["day"] - timedelta(days=1), time(23, 0))
    a = await book(pool, world, "10:30", now=night)
    conf = next(r for r in await job_rows(pool, a["id"]) if r["type"] == "confirmation")
    assert to_ist(conf["due_at"]).strftime("%a %H:%M") == "Mon 08:00"


async def test_moving_cancels_old_jobs_and_creates_new_ones(pool, world):
    now = frozen(world)
    a = await book(pool, world, "10:30", now=now)
    async with pool.connection() as conn:
        await svc.move_appointment(conn, clinic_id=world["clinic"], appt_id=a["id"],
                                   start_at=at(world["day"], "17:30"), now=now)
    rows = await job_rows(pool, a["id"])
    assert sum(r["status"] == "cancelled" for r in rows) == 3
    new = [r for r in rows if r["status"] == "scheduled"]
    assert {r["type"] for r in new} == {"confirmation", "day_before", "two_hour"}   # incl. a fresh confirmation
    assert to_ist(next(r for r in new if r["type"] == "two_hour")["due_at"]).strftime("%H:%M") == "15:30"


async def test_cancelling_cancels_scheduled_jobs(pool, world):
    a = await book(pool, world, "10:30", now=frozen(world))
    async with pool.connection() as conn:
        await svc.set_status(conn, clinic_id=world["clinic"], appt_id=a["id"], to_status="cancelled")
    assert {r["status"] for r in await job_rows(pool, a["id"])} == {"cancelled"}


# ───────────────────────── the tick (spec 7.2) ─────────────────────────
async def test_tick_sends_due_jobs_within_a_minute_and_never_twice(pool, world, messaging):
    now = frozen(world)
    for doc, pat in (("neha", "p1"), ("arjun", "p2")):       # morning slots only: their 24h reminders are already overdue
        for s in ["10:00", "10:30", "11:00"]:
            await book(pool, world, s, doctor=doc, patient=pat, now=now)
    c1 = await jobs.tick(pool, messaging, now + timedelta(seconds=30))
    assert c1["sent"] == 6
    assert not (await jobs.tick(pool, messaging, now + timedelta(seconds=45)))["sent"]          # nothing re-sent
    assert (await jobs.tick(pool, messaging, now + timedelta(minutes=6)))["sent"] == 6          # the day_before jobs
    sent = await q(pool, "select count(*) as n, count(distinct (appointment_id, template_name)) as d from outbound_message")
    assert (sent[0]["n"], sent[0]["d"]) == (12, 12)
    late = await q(pool, "select count(*) as n from reminder_job where status='sent' and sent_at - due_at > interval '60 seconds'")
    assert late[0]["n"] == 0


async def test_two_concurrent_ticks_never_send_a_job_twice(pool, world, messaging):
    now = frozen(world)
    for i, s in enumerate(["10:00", "10:30", "11:00", "11:30"]):
        await book(pool, world, s, doctor="neha", now=now)
        await book(pool, world, s, doctor="arjun", patient="p2", now=now)
    results = await asyncio.gather(*[jobs.tick(pool, messaging, now + timedelta(seconds=30)) for _ in range(3)])
    assert sum(r["sent"] for r in results) == 8
    dup = await q(pool, "select appointment_id, template_name from outbound_message group by 1,2 having count(*) > 1")
    assert dup == []
    assert (await q(pool, "select count(*) as n from outbound_message"))[0]["n"] == 8


async def test_execution_rechecks_state_cancelled_confirmed_started(pool, world, messaging):
    now = frozen(world)
    a = await book(pool, world, "10:00", now=now)
    b = await book(pool, world, "10:30", patient="p2", now=now)
    await jobs.tick(pool, messaging, now + timedelta(seconds=30))               # confirmations out
    async with pool.connection() as conn:
        await svc.set_status(conn, clinic_id=world["clinic"], appt_id=a["id"], to_status="confirmed")
        await conn.execute("update appointment set status='cancelled' where id=%s", [b["id"]])  # bypass: simulate drift
    await jobs.tick(pool, messaging, now + timedelta(minutes=6))
    ra = {r["type"]: r for r in await job_rows(pool, a["id"])}
    rb = {r["type"]: r for r in await job_rows(pool, b["id"])}
    assert (ra["day_before"]["status"], ra["day_before"]["skip_reason"]) == ("skipped", "already_confirmed")
    assert (rb["day_before"]["status"], rb["day_before"]["skip_reason"]) == ("skipped", "appointment_inactive")


async def test_post_visit_then_recall(pool, world, messaging):
    await q(pool, "update treatment_type set recall_after_days=180 where id=%s returning id", [world["consult"]])
    await q(pool, "update clinic set review_link='https://g.page/r/test/review' where id=%s returning id", [world["clinic"]])
    a = await book(pool, world, "10:00", now=frozen(world))
    arrived_at = ist_to_utc(world["day"], time(10, 5))
    async with pool.connection() as conn:
        await svc.set_status(conn, clinic_id=world["clinic"], appt_id=a["id"], to_status="arrived", now=arrived_at)
    pv = next(r for r in await job_rows(pool, a["id"]) if r["type"] == "post_visit")
    assert to_ist(pv["due_at"]).strftime("%H:%M") == "10:50"                   # end 10:20 + 30 min
    assert (await jobs.tick(pool, messaging, ist_to_utc(world["day"], time(10, 49))))["sent"] == 0
    sent_at = ist_to_utc(world["day"], time(10, 51))
    assert (await jobs.tick(pool, messaging, sent_at))["sent"] == 1
    body = (await q(pool, "select rendered_body from outbound_message where template_name='showup_post_visit'"))[0]["rendered_body"]
    assert body.startswith("Thank you for visiting Test Clinic today, Patient.") and "https://g.page/r/test/review" in body
    rc = next(r for r in await job_rows(pool, a["id"]) if r["type"] == "recall")
    assert to_ist(rc["due_at"]).strftime("%H:%M") == "10:30"
    assert to_ist(rc["due_at"]).date() == world["day"] + timedelta(days=180)


async def test_post_visit_without_review_link_is_skipped(pool, world, messaging):
    a = await book(pool, world, "10:00", now=frozen(world))
    async with pool.connection() as conn:
        await svc.set_status(conn, clinic_id=world["clinic"], appt_id=a["id"], to_status="arrived",
                             now=ist_to_utc(world["day"], time(10, 5)))
    await jobs.tick(pool, messaging, ist_to_utc(world["day"], time(11, 0)))
    pv = next(r for r in await job_rows(pool, a["id"]) if r["type"] == "post_visit")
    assert (pv["status"], pv["skip_reason"]) == ("skipped", "no_review_link")


async def test_daily_summary_per_doctor_at_8am_once(pool, world, messaging):
    now = frozen(world)
    await book(pool, world, "10:00", now=now)
    await book(pool, world, "10:30", patient="p2", status="confirmed", now=now)
    before = ist_to_utc(world["day"], time(7, 59, 30))
    assert (await jobs.tick(pool, messaging, before))["summaries_created"] == 4   # 2 doctors x (today, tomorrow)
    await jobs.tick(pool, messaging, ist_to_utc(world["day"], time(8, 0, 10)))
    rows = await q(pool, """select d.name, m.rendered_body, m.transport from outbound_message m
                            join doctor d on d.id=m.doctor_id where m.template_name='showup_daily_summary' order by d.name""")
    assert [r["name"] for r in rows] == ["Dr. Arjun", "Dr. Neha"]
    neha = rows[1]["rendered_body"]
    assert neha.startswith("Good morning Dr. Neha. Today at Mon ") and "2 appointments, 1 confirmed, 1 not yet confirmed" in neha
    assert "Open slots: 10:50, 17:00." in neha and "\n" not in neha and rows[1]["transport"] == "simulator"
    again = await jobs.tick(pool, messaging, ist_to_utc(world["day"], time(8, 0, 20)))
    assert again["summaries_created"] == 0
    assert (await q(pool, "select count(*) as n from outbound_message where doctor_id is not null"))[0]["n"] == 2


# ───────────────────────── guards in the pipeline ─────────────────────────
async def test_after_stop_every_send_type_is_skipped_as_opted_out(pool, world, messaging):
    now = frozen(world)
    a = await book(pool, world, "10:00", patient="p2", now=now)
    for typ in ("post_visit", "recall", "two_hour"):
        await q(pool, """insert into reminder_job (clinic_id, appointment_id, patient_id, doctor_id, type, due_at)
                         values (%s,%s,%s,%s,%s,%s) returning id""",
                [world["clinic"], a["id"], world["p2"], world["neha"], typ, now])
    await q(pool, "update patient set opted_out=true, opted_out_at=now() where id=%s returning id", [world["p2"]])
    await jobs.tick(pool, messaging, now + timedelta(minutes=6))
    rows = [r for r in await job_rows(pool, a["id"]) if r["due_at"] <= now + timedelta(minutes=6)]   # the due ones
    assert {r["type"] for r in rows} == {"confirmation", "day_before", "two_hour", "post_visit", "recall"}
    assert all((r["status"], r["skip_reason"]) == ("skipped", "opted_out") for r in rows)
    assert (await q(pool, "select count(*) as n from outbound_message"))[0]["n"] == 0
    clinic, patient = await clinic_and(pool, world, "p2")
    async with pool.connection() as conn:
        for out in (Outgoing("template", "Hi"), Outgoing("text", "Hi"), Outgoing("interactive_list", "Hi", list={"sections": []}),
                    Outgoing("interactive_buttons", "Hi")):
            r = await messaging.send(conn, clinic, out, patient=patient, proactive=False)
            assert (r.outcome, r.reason) == ("skipped", "opted_out"), out.kind


async def test_medical_content_blocks_the_send_and_fails_the_job(pool, world, messaging):
    await q(pool, "update clinic set name='Root Canal Specialists' where id=%s returning id", [world["clinic"]])
    now = frozen(world)
    a = await book(pool, world, "10:00", now=now)
    await jobs.tick(pool, messaging, now + timedelta(seconds=30))
    conf = next(r for r in await job_rows(pool, a["id"]) if r["type"] == "confirmation")
    assert (conf["status"], conf["skip_reason"]) == ("failed", "medical_content")
    assert (await q(pool, "select count(*) as n from outbound_message"))[0]["n"] == 0


async def test_treatment_name_in_a_body_is_blocked(pool, world, messaging):
    clinic, patient = await clinic_and(pool, world)
    async with pool.connection() as conn:
        r = await messaging.send(conn, clinic, Outgoing("template", "Hi Patient, your Consultation is booked"),
                                 patient=patient)
    assert (r.outcome, r.reason) == ("blocked", "medical_content")


async def test_seeded_patients_are_forced_onto_the_simulator_even_in_live_mode(pool, world, messaging):
    await q(pool, "update clinic set messaging_mode='live' where id=%s returning id", [world["clinic"]])
    await q(pool, "update patient set is_demo_seed=true where id=%s returning id", [world["p1"]])
    now = frozen(world)
    await book(pool, world, "10:00", patient="p1", now=now)
    await book(pool, world, "10:30", patient="p2", now=now)
    await jobs.tick(pool, messaging, now + timedelta(seconds=30))
    rows = {r["patient_id"]: r for r in await q(pool, "select patient_id, transport, status, error from outbound_message")}
    assert rows[world["p1"]]["transport"] == "simulator" and rows[world["p1"]]["status"] == "sent"
    # a real patient in live mode really tries the live transport; with no WA credentials configured it fails loudly
    assert rows[world["p2"]]["transport"] == "live" and rows[world["p2"]]["status"] == "failed"
    assert "live_not_configured" in rows[world["p2"]]["error"]


async def test_session_messages_need_an_open_24h_window(pool, world, messaging):
    clinic, patient = await clinic_and(pool, world)
    out = Outgoing("text", "Confirmed ✅ See you on Mon 7 Jan at 10:00 AM.")
    async with pool.connection() as conn:
        r = await messaging.send(conn, clinic, out, patient=patient, proactive=False)
    assert (r.outcome, r.reason) == ("skipped", "window_closed")
    await webhook.handle_payload(pool, mp.text_payload(patient["phone"], "hello", msg_id="in-1"), messaging)
    async with pool.connection() as conn:
        assert (await messaging.send(conn, clinic, out, patient=patient, proactive=False)).outcome == "sent"
    # a tap older than 24h does not count
    async with pool.connection() as conn:
        await conn.execute("update message_event set created_at = now() - interval '25 hours'")
        assert (await messaging.send(conn, clinic, out, patient=patient, proactive=False)).reason == "window_closed"


async def test_quiet_hours_block_direct_proactive_sends(pool, world, messaging):
    clinic, patient = await clinic_and(pool, world)
    night = ist_to_utc(world["day"], time(22, 0))
    async with pool.connection() as conn:
        out = Outgoing("template", "Hi Patient, your appointment is booked.")
        r = await messaging.send(conn, clinic, out, patient=patient, now=night)
        assert (r.outcome, r.reason) == ("skipped", "quiet_hours")
        assert (await messaging.send(conn, clinic, out, patient=patient, now=night, ignore_quiet_hours=True)).outcome == "sent"


# ───────────────────────── webhook, dedupe, statuses ─────────────────────────
async def test_webhook_handshake_and_signature(client, world):
    ok = await client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                                        "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert (await client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                           "hub.challenge": "1"})).status_code == 403
    payload = mp.text_payload("+919876500000", "hi", msg_id="sig-1")
    assert (await post_signed(client, payload)).status_code == 200
    assert (await post_signed(client, payload, secret="wrong")).status_code == 403
    assert (await client.post("/webhooks/whatsapp", json=payload)).status_code == 403   # unsigned


async def test_same_payload_twice_changes_state_once(pool, world, messaging):
    p = mp.text_payload("+919876500000", "hello there", msg_id="dup-1")
    first = await webhook.handle_payload(pool, p, messaging)
    second = await webhook.handle_payload(pool, p, messaging)
    assert first[0]["result"] == "processed" and second[0]["result"] == "duplicate"
    assert (await q(pool, "select count(*) as n from message_event where dedupe_key='msg:dup-1'"))[0]["n"] == 1


async def test_concurrent_identical_deliveries_process_exactly_once(pool, world, messaging):
    p = mp.button_payload("+919876500000", "CONFIRM:x", "Confirm", msg_id="dup-2")
    res = await asyncio.gather(*[webhook.handle_payload(pool, p, messaging) for _ in range(6)])
    assert sorted(r[0]["result"] for r in res) == ["duplicate"] * 5 + ["processed"]


async def test_status_progression_never_goes_backwards_and_failures_keep_meta_codes(pool, world, messaging):
    clinic, patient = await clinic_and(pool, world)
    async with pool.connection() as conn:
        await messaging.send(conn, clinic, Outgoing("template", "Hi Patient, hello."), patient=patient, proactive=False)
    wamid, phone = (lambda r: (r["wa_message_id"], patient["phone"]))((await q(pool, "select wa_message_id from outbound_message"))[0])
    status = lambda: q(pool, "select status, error from outbound_message")  # noqa: E731
    assert (await status())[0]["status"] == "sent"
    for st in ("delivered", "read", "delivered"):                       # late 'delivered' after 'read'
        await messaging.emit_status(wamid, st, phone)
    assert (await status())[0]["status"] == "read"
    await webhook.handle_payload(pool, mp.status_payload(wamid, "failed", phone, [{"code": 131026, "title": "Undeliverable"}]))
    row = (await status())[0]
    assert row["status"] == "failed" and "131026" in row["error"]


# ───────────────────────── simulator & demo endpoints ─────────────────────────
async def test_webhook_and_simulator_inbound_behave_identically(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    phone = "+919876500000"
    builders = {
        "button": lambda i: mp.button_payload(phone, "CONFIRM:zzz", "Confirm", msg_id=i),
        "list_reply": lambda i: mp.list_reply_payload(phone, "RS:a:d:1", "Tue 10 Nov · 10:30 AM", "Dr. Neha", msg_id=i),
        "button_reply": lambda i: mp.button_reply_payload(phone, "WL_BOOK:o", "Book", msg_id=i),
        "text": lambda i: mp.text_payload(phone, "can I come earlier?", msg_id=i),
    }
    for name, build in builders.items():
        r1 = await post_signed(client, build(f"{name}-hook"))
        r2 = await client.post("/simulator/inbound", json=build(f"{name}-sim"), headers=h)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r2.json()["results"][0]["result"] == "processed"
        rows = await q(pool, "select dedupe_key, event, payload from message_event where dedupe_key like %s order by dedupe_key",
                       [f"msg:{name}-%"])
        assert len(rows) == 2 and rows[0]["event"] == rows[1]["event"] and rows[0]["payload"] == rows[1]["payload"], name
    # statuses too
    clinic, patient = await clinic_and(pool, world)
    async with pool.connection() as conn:
        for _ in range(2):
            await messaging.send(conn, clinic, Outgoing("template", "Hi Patient, hello."), patient=patient, proactive=False)
    w1, w2 = [r["wa_message_id"] for r in await q(pool, "select wa_message_id from outbound_message where kind='template' order by created_at")]
    await post_signed(client, mp.status_payload(w1, "delivered", phone))
    await client.post("/simulator/inbound", json=mp.status_payload(w2, "delivered", phone), headers=h)
    assert [r["status"] for r in await q(pool, "select status from outbound_message where kind='template' order by created_at")] == ["delivered"] * 2


async def test_simulator_inbound_requires_staff_login(client, world):
    assert (await client.post("/simulator/inbound", json={})).status_code == 401


async def test_fire_now_and_simulator_thread_flow(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30", status="confirmed", now=frozen(world))
    r = await client.post("/demo/fire", json={"appointment_id": str(a["id"]), "type": "day_before"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "sent", r.text      # force ignores already_confirmed
    threads = (await client.get("/simulator/threads", headers=h)).json()
    assert [t["name"] for t in threads] == ["Patient 1"] and threads[0]["kind"] == "patient"
    base = f"/simulator/threads/patient/{world['p1']}"
    msgs = (await client.get(f"{base}/messages", headers=h)).json()["messages"]
    out = msgs[-1]
    assert out["direction"] == "out" and out["consumed"] is False and out["status"] == "sent"
    assert [b["payload"] for b in out["buttons"]] == [f"CONFIRM:{a['id']}", f"RESCHEDULE:{a['id']}", f"CANCEL:{a['id']}"]
    assert out["transport"] == "simulator" and "tomorrow" in out["body"] and out["body"].startswith("Hi Patient,")
    # the presenter taps Reschedule in the simulator (Meta-shaped payload, with the message it replies to)
    tap = mp.button_payload("+919876500000", f"RESCHEDULE:{a['id']}", "Reschedule", context_wamid=out["wa_message_id"])
    assert (await client.post("/simulator/inbound", json=tap, headers=h)).status_code == 200
    msgs = (await client.get(f"{base}/messages", headers=h)).json()["messages"]
    # Phase 3: the tap now triggers the reschedule slot list, which arrives AFTER the tap in the chat
    assert [m["direction"] for m in msgs] == ["out", "in", "out"]
    assert msgs[0]["consumed"] is True and msgs[1]["body"] == "Reschedule" and msgs[2]["kind"] == "interactive_list"
    assert msgs[2]["list"]["button"] == "See slots" and msgs[2]["consumed"] is False
    assert (await client.post(f"{base}/read", headers=h)).json()["marked"] == 2
    assert [m["status"] for m in (await client.get(f"{base}/messages", headers=h)).json()["messages"] if m["direction"] == "out"] == ["read", "read"]


async def test_fire_now_still_respects_opt_out(client, pool, world):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30", now=frozen(world))
    await q(pool, "update patient set opted_out=true where id=%s returning id", [world["p1"]])
    r = await client.post("/demo/fire", json={"appointment_id": str(a["id"]), "type": "confirmation"}, headers=h)
    assert (r.json()["status"], r.json()["reason"]) == ("skipped", "opted_out")


async def test_internal_tick_requires_the_secret(client, world):
    assert (await client.post("/internal/tick")).status_code == 403
    assert (await client.post("/internal/tick", headers={"X-Internal-Secret": "wrong"})).status_code == 403
    ok = await client.post("/internal/tick", headers={"X-Internal-Secret": "tick-secret"})
    assert ok.status_code == 200 and isinstance(ok.json(), dict)


async def test_dev_login_issues_a_working_token(client, world):
    r = await client.post("/auth/dev-login", json={"role": "owner"})
    assert r.status_code == 200
    me = await client.get("/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["role"] == "owner" and me.json()["email"] == "owner@smilecare.demo"
