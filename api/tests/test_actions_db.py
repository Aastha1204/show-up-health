"""Patient actions end to end (spec 7.3): Confirm, Cancel, Reschedule list flow, free text, STOP.
Real Postgres; taps are Meta-shaped payloads through the same handler the webhook uses."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import jobs
from app.messaging import inbound, meta_payloads as mp, webhook
from app.reminders import fmt_date, fmt_time
from app.services import appointments as svc, reschedule
from app.timeutil import utcnow
from .conftest import at, token_for
from .test_messaging_db import book, clinic_and, frozen, job_rows, post_signed, q

pytestmark = pytest.mark.db

PHONES = {"p1": "+919876500000", "p2": "+919876500001", "p3": "+919876500002"}


async def tap(pool, messaging, patient, payload, text="Tap", ctx_wamid=None):
    return (await webhook.handle_payload(pool, mp.button_payload(PHONES[patient], payload, text, ctx_wamid), messaging))[0]


async def say(pool, messaging, patient, text):
    return (await webhook.handle_payload(pool, mp.text_payload(PHONES[patient], text), messaging))[0]


async def pick(pool, messaging, patient, row_id, title="Slot"):
    return (await webhook.handle_payload(pool, mp.list_reply_payload(PHONES[patient], row_id, title), messaging))[0]


async def sent(pool, w, patient="p1"):
    """(kind, body) of everything sent to the patient, in order."""
    rows = await q(pool, "select kind, rendered_body, template_name, list from outbound_message where patient_id=%s order by created_at", [w[patient]])
    return [(r["kind"], r["rendered_body"]) for r in rows]


async def status_of(pool, appt_id):
    return (await q(pool, "select status, start_at, doctor_id from appointment where id=%s", [appt_id]))[0]


async def events(pool, appt_id):
    return [(e["to_status"], e["actor"]) for e in await q(
        pool, "select to_status, actor from appointment_event where appointment_id=%s order by created_at", [appt_id])]


async def last_list(pool, w, patient="p1"):
    rows = await q(pool, "select list from outbound_message where patient_id=%s and kind='interactive_list' order by created_at desc limit 1", [w[patient]])
    return rows[0]["list"] if rows else None


def rows_of(lst):
    return [r for s in lst["sections"] for r in s["rows"]]


# ───────────────────────── Confirm ─────────────────────────
async def test_confirm_updates_status_replies_and_repeat_taps_are_noops(pool, world, messaging):
    a = await book(pool, world, "10:30")
    r = await tap(pool, messaging, "p1", f"CONFIRM:{a['id']}", "Confirm")
    assert r["outcome"] == "confirmed"
    assert (await status_of(pool, a["id"]))["status"] == "confirmed"
    assert (await sent(pool, world)) == [("text", f"Confirmed ✅ See you on {fmt_date(a['start_at'])} at 10:30 AM.")]
    assert (await events(pool, a["id"]))[-1] == ("confirmed", "patient")
    again = await tap(pool, messaging, "p1", f"CONFIRM:{a['id']}", "Confirm")      # a NEW message id: a genuine second tap
    assert again["outcome"] == "already_confirmed"
    assert len(await sent(pool, world)) == 1 and len(await events(pool, a["id"])) == 2


async def test_confirm_works_from_rescheduled(pool, world, messaging):
    a = await book(pool, world, "10:30")
    await q(pool, "update appointment set status='rescheduled' where id=%s returning id", [a["id"]])
    assert (await tap(pool, messaging, "p1", f"CONFIRM:{a['id']}"))["outcome"] == "confirmed"


async def test_button_without_payload_falls_back_to_text_and_latest_active_appointment(pool, world, messaging):
    later = await book(pool, world, "17:00")
    sooner = await book(pool, world, "10:30", patient="p1", doctor="arjun")
    r = await tap(pool, messaging, "p1", "", "Confirm")                      # Meta sent no payload
    assert r["outcome"] == "confirmed"
    assert (await status_of(pool, sooner["id"]))["status"] == "confirmed"      # the soonest upcoming one
    assert (await status_of(pool, later["id"]))["status"] == "pending"


async def test_tap_on_foreign_or_malformed_appointment_reveals_and_changes_nothing(pool, world, messaging):
    a = await book(pool, world, "10:30", patient="p1")
    r = await tap(pool, messaging, "p2", f"CONFIRM:{a['id']}")                 # p2 taps p1's button
    assert r["outcome"] == "inactive"
    assert (await status_of(pool, a["id"]))["status"] == "pending"
    assert (await sent(pool, world, "p2")) == [("text", inbound.INACTIVE_TEXT)]
    assert (await tap(pool, messaging, "p2", "CONFIRM:not-a-uuid"))["outcome"] == "inactive"


@pytest.mark.parametrize("how", ["arrived", "cancelled", "no_show", "past"])
async def test_tap_on_inactive_or_past_appointment_says_so_and_changes_nothing(pool, world, messaging, how):
    if how == "past":
        async with pool.connection() as conn:
            a = await svc.create_appointment(conn, clinic_id=world["clinic"], patient_id=world["p1"], doctor_id=world["neha"],
                                             treatment_type_id=world["consult"], start_at=utcnow() - timedelta(days=1),
                                             enforce_hours=False, schedule_reminders=False)
        expect = "pending"
    else:
        a = await book(pool, world, "10:30")
        await q(pool, "update appointment set status=%s where id=%s returning id", [how, a["id"]])
        expect = how
    for verb in ("CONFIRM", "CANCEL", "RESCHEDULE"):
        assert (await tap(pool, messaging, "p1", f"{verb}:{a['id']}"))["outcome"] == "inactive"
    assert (await status_of(pool, a["id"]))["status"] == expect
    assert [b for k, b in await sent(pool, world)] == [inbound.INACTIVE_TEXT] * 3
    assert await last_list(pool, world) is None                                # no slot list for a dead appointment


# ───────────────────────── Cancel ─────────────────────────
async def test_cancel_updates_status_cancels_jobs_replies_and_notifies_waitlist(pool, world, messaging, monkeypatch):
    calls = []

    async def fake(conn, msgr, clinic, doctor_id, start_at, duration_min, now):
        calls.append((doctor_id, start_at, duration_min))
    monkeypatch.setattr(inbound.waitlist, "offer_freed_slot", fake)
    a = await book(pool, world, "10:30", now=frozen(world))
    r = await tap(pool, messaging, "p1", f"CANCEL:{a['id']}", "Cancel")
    assert r["outcome"] == "cancelled" and (await status_of(pool, a["id"]))["status"] == "cancelled"
    assert (await sent(pool, world)) == [("text", "Your appointment has been cancelled. Reply here any time to book again.")]
    assert {j["status"] for j in await job_rows(pool, a["id"])} == {"cancelled"}
    assert calls == [(world["neha"], a["start_at"], 20)]                       # the freed slot goes to the waitlist engine
    assert (await events(pool, a["id"]))[-1] == ("cancelled", "patient")


async def test_second_cancel_tap_is_a_noop_with_the_inactive_message(pool, world, messaging):
    a = await book(pool, world, "10:30")
    await tap(pool, messaging, "p1", f"CANCEL:{a['id']}")
    r = await tap(pool, messaging, "p1", f"CANCEL:{a['id']}")
    assert r["outcome"] == "inactive"
    assert [e for e in await events(pool, a["id"]) if e[0] == "cancelled"] == [("cancelled", "patient")]


async def test_cancel_frees_the_slot_for_someone_else(pool, world, messaging):
    a = await book(pool, world, "10:30")
    await tap(pool, messaging, "p1", f"CANCEL:{a['id']}")
    await book(pool, world, "10:30", patient="p2")                             # no conflict: the slot is free again


# ───────────────────────── Reschedule list ─────────────────────────
async def test_reschedule_sends_a_valid_list_of_free_future_slots(pool, world, messaging):
    a = await book(pool, world, "10:30")
    r = await tap(pool, messaging, "p1", f"RESCHEDULE:{a['id']}", "Reschedule")
    assert r["outcome"] == "reschedule_list_sent"
    lst = await last_list(pool, world)
    rows = rows_of(lst)
    assert lst["header"] == "Pick a new time" and lst["button"] == "See slots"
    assert 2 <= len(rows) <= 10 and rows[-1]["id"] == f"RS_NONE:{a['id']}" and rows[-1]["title"] == "None of these work"
    slots = rows[:-1]
    assert len(slots) == 9                                                      # plenty of free time: 9 slots + "none"
    assert all(len(x["title"]) <= 24 and len(x["description"]) <= 72 and len(x["id"]) <= 200 for x in rows)
    now, parsed = utcnow(), [reschedule.parse_row_id(x["id"]) for x in slots]
    assert all(p is not None and p[0] == a["id"] for p in parsed)
    starts = [p[2] for p in parsed]
    assert all(now + timedelta(hours=2) <= s < now + timedelta(days=7) for s in starts)   # >=2h out, within 7 days
    assert all(p[1] == world["neha"] for p in parsed)                           # same doctor first
    assert all(s != a["start_at"] for s in starts) and starts == sorted(starts)
    per_day = {}
    for s in starts:
        per_day[fmt_date(s)] = per_day.get(fmt_date(s), 0) + 1
    assert max(per_day.values()) <= 3 and len(per_day) >= 3                     # spans several days
    assert [s["title"] for s in lst["sections"][:-1]] == list(per_day)          # sections are the dates, in order
    assert all(r["title"].startswith(sec["title"]) for sec in lst["sections"][:-1] for r in sec["rows"])
    assert (await status_of(pool, a["id"]))["status"] == "pending"              # offering slots changes nothing


async def test_offered_slots_are_never_taken_ones(pool, world, messaging):
    a = await book(pool, world, "10:30")
    first = rows_of(await _list_after_reschedule(pool, world, messaging, a))[0]
    _, doc, start = reschedule.parse_row_id(first["id"])
    await book_at(pool, world, "p2", doc, start)
    await tap(pool, messaging, "p1", f"RESCHEDULE:{a['id']}")
    new_ids = {x["id"] for x in rows_of(await last_list(pool, world))}
    assert first["id"] not in new_ids


async def _list_after_reschedule(pool, w, messaging, appt):
    await tap(pool, messaging, "p1", f"RESCHEDULE:{appt['id']}")
    return await last_list(pool, w)


async def book_at(pool, w, patient, doctor_id, start):
    async with pool.connection() as conn:
        return await svc.create_appointment(conn, clinic_id=w["clinic"], patient_id=w[patient], doctor_id=doctor_id,
                                            treatment_type_id=w["consult"], start_at=start)


async def test_no_free_time_tells_the_patient_and_alerts_reception(pool, world, messaging):
    a = await book(pool, world, "10:30")
    await q(pool, "update doctor set active=false where id=%s returning id", [world["arjun"]])
    await q(pool, "update doctor set working_hours='{}' where id=%s returning id", [world["neha"]])
    assert (await tap(pool, messaging, "p1", f"RESCHEDULE:{a['id']}"))["outcome"] == "reschedule_no_slots"
    assert [b for k, b in await sent(pool, world)] == [inbound.NO_SLOTS_TEXT]
    assert len(await q(pool, "select 1 from inbox_item")) == 1


# ───────────────────────── Pick a slot ─────────────────────────
async def test_picking_a_slot_moves_the_appointment_and_sends_a_fresh_confirmation(pool, world, messaging, monkeypatch):
    calls = []

    async def fake(conn, msgr, clinic, doctor_id, start_at, duration_min, now):
        calls.append((doctor_id, start_at))
    monkeypatch.setattr(inbound.waitlist, "offer_freed_slot", fake)
    a = await book(pool, world, "10:30")
    lst = await _list_after_reschedule(pool, world, messaging, a)
    row = rows_of(lst)[1]
    _, doc, start = reschedule.parse_row_id(row["id"])
    r = await pick(pool, messaging, "p1", row["id"], row["title"])
    assert r["outcome"] == "rescheduled"
    cur = await status_of(pool, a["id"])
    assert (cur["status"], cur["start_at"], cur["doctor_id"]) == ("rescheduled", start, doc)
    assert (await events(pool, a["id"]))[-1] == ("rescheduled", "patient")
    kind, body = (await sent(pool, world))[-1]
    assert kind == "template" and body.startswith("Hi Patient, your appointment at Test Clinic is booked for "
                                                  f"{fmt_date(start)} at {fmt_time(start)} with Dr. Neha")
    jobs_now = await job_rows(pool, a["id"])
    assert sorted((j["type"], j["status"]) for j in jobs_now if j["status"] != "cancelled") == [
        ("confirmation", "sent"), ("day_before", "scheduled"), ("two_hour", "scheduled")]
    assert sum(j["status"] == "cancelled" for j in jobs_now) == 3                # the old trio
    assert calls == [(world["neha"], a["start_at"])]                             # the old slot goes to the waitlist


async def test_picking_a_slot_someone_just_took_apologises_and_resends(pool, world, messaging):
    a = await book(pool, world, "10:30")
    lst = await _list_after_reschedule(pool, world, messaging, a)
    row = rows_of(lst)[0]
    _, doc, start = reschedule.parse_row_id(row["id"])
    await book_at(pool, world, "p2", doc, start)                                # taken in the meantime
    r = await pick(pool, messaging, "p1", row["id"], row["title"])
    assert r["outcome"] == "taken_list_sent"
    cur = await status_of(pool, a["id"])
    assert (cur["status"], cur["start_at"]) == ("pending", a["start_at"])        # untouched
    tail = (await sent(pool, world))[-2:]
    assert tail[0] == ("text", "Sorry, that time was just taken. Here are the latest options.")
    assert tail[1][0] == "interactive_list"
    assert row["id"] not in {x["id"] for x in rows_of(await last_list(pool, world))}


async def test_two_patients_picking_the_same_slot_exactly_one_gets_it(pool, world, messaging):
    a1 = await book(pool, world, "10:30", patient="p1")
    a2 = await book(pool, world, "17:30", patient="p2")
    await tap(pool, messaging, "p1", f"RESCHEDULE:{a1['id']}")
    lst = await last_list(pool, world, "p1")
    row = rows_of(lst)[0]
    _, doc, start = reschedule.parse_row_id(row["id"])
    rid2 = reschedule.row_id(a2["id"], doc, start)                               # p2 picks the very same slot
    res = await asyncio.gather(pick(pool, messaging, "p1", row["id"]), pick(pool, messaging, "p2", rid2))
    assert sorted(r["outcome"] for r in res) == ["rescheduled", "taken_list_sent"]
    starts = [(await status_of(pool, x["id"]))["start_at"] for x in (a1, a2)]
    assert starts.count(start) == 1


async def test_stale_pick_inside_two_hours_gets_a_fresh_list(pool, world, messaging):
    a = await book(pool, world, "10:30")
    soon = utcnow() + timedelta(hours=1)
    r = await pick(pool, messaging, "p1", reschedule.row_id(a["id"], world["neha"], soon))
    assert r["outcome"] == "stale_list_sent"
    assert (await status_of(pool, a["id"]))["start_at"] == a["start_at"]
    assert (await sent(pool, world))[0] == ("text", inbound.STALE_TEXT)


async def test_pick_for_a_cancelled_appointment_is_inactive(pool, world, messaging):
    a = await book(pool, world, "10:30")
    lst = await _list_after_reschedule(pool, world, messaging, a)
    await tap(pool, messaging, "p1", f"CANCEL:{a['id']}")
    r = await pick(pool, messaging, "p1", rows_of(lst)[0]["id"])
    assert r["outcome"] == "inactive" and (await status_of(pool, a["id"]))["status"] == "cancelled"


async def test_none_of_these_work_goes_to_reception_not_the_system(pool, world, messaging):
    a = await book(pool, world, "10:30")
    r = await pick(pool, messaging, "p1", f"RS_NONE:{a['id']}", "None of these work")
    assert r["outcome"] == "none_of_these"
    items = await q(pool, "select text, handled from inbox_item")
    assert len(items) == 1 and "None of the offered reschedule times work" in items[0]["text"] and items[0]["handled"] is False
    assert (await sent(pool, world)) == [("text", inbound.ACK_TEXT)]
    assert (await status_of(pool, a["id"]))["status"] == "pending"


async def test_malformed_pick_is_ignored_safely(pool, world, messaging):
    for bad in ["RS:garbage", "RS:a:b:c", "RS_NONE:nope"]:
        assert (await pick(pool, messaging, "p1", bad))["result"] == "processed"


# ───────────────────────── Free text ─────────────────────────
async def test_free_text_goes_to_the_inbox_with_one_ack_per_12_hours(pool, world, messaging):
    assert (await say(pool, messaging, "p1", "Can I come a bit earlier?"))["outcome"] == "inbox"
    assert (await sent(pool, world)) == [("text", "Thanks, our reception team will reply shortly.")]
    await say(pool, messaging, "p1", "Hello? Anyone there?")
    assert [r["text"] for r in await q(pool, "select text from inbox_item order by created_at")] == [
        "Can I come a bit earlier?", "Hello? Anyone there?"]
    assert len(await sent(pool, world)) == 1                                    # still just one ack
    await q(pool, "update outbound_message set created_at = now() - interval '13 hours' returning id")
    await say(pool, messaging, "p1", "Still waiting")
    assert len(await sent(pool, world)) == 2                                    # 12h passed: acknowledged again


async def test_inbox_item_belongs_to_the_patient_and_is_unhandled(pool, world, messaging):
    await say(pool, messaging, "p2", "Please call me")
    (row,) = await q(pool, "select patient_id, handled, handled_by, clinic_id from inbox_item")
    assert (row["patient_id"], row["handled"], row["handled_by"], row["clinic_id"]) == (world["p2"], False, None, world["clinic"])


# ───────────────────────── STOP ─────────────────────────
@pytest.mark.parametrize("word", ["STOP", "stop", "Stop.", "UNSUBSCRIBE", "unsubscribe", "please stop", "बंद", "थांबा"])
async def test_stop_opts_out_and_confirms(pool, world, messaging, word):
    r = await say(pool, messaging, "p1", word)
    assert r["outcome"] == "opted_out"
    (p,) = await q(pool, "select opted_out, opted_out_at from patient where id=%s", [world["p1"]])
    assert p["opted_out"] is True and p["opted_out_at"] is not None
    assert (await sent(pool, world)) == [("text", "You will not receive further WhatsApp updates from Test Clinic.")]
    assert (await q(pool, "select count(*) as n from inbox_item"))[0]["n"] == 0      # a STOP is not an inbox message


async def test_after_stop_nothing_is_sent_and_repeats_are_silent(pool, world, messaging):
    a = await book(pool, world, "10:30", now=frozen(world))
    await say(pool, messaging, "p1", "STOP")
    assert (await say(pool, messaging, "p1", "STOP"))["outcome"] == "already_opted_out"
    await say(pool, messaging, "p1", "why am I still getting messages")            # reaches reception, no auto-ack
    assert len(await sent(pool, world)) == 1
    assert (await q(pool, "select count(*) as n from inbox_item"))[0]["n"] == 1
    await jobs.tick(pool, messaging, frozen(world) + timedelta(minutes=10))
    rows = await job_rows(pool, a["id"])
    assert all((j["status"], j["skip_reason"]) == ("skipped", "opted_out") for j in rows if j["type"] in ("confirmation", "day_before"))
    assert len(await sent(pool, world)) == 1                                       # only the STOP confirmation, ever


@pytest.mark.parametrize("text", ["can you stop by at 5 pm?", "don't stop the reminders", "my phone stopped working"])
async def test_sentences_that_merely_contain_stop_do_not_opt_out(pool, world, messaging, text):
    assert (await say(pool, messaging, "p1", text))["outcome"] == "inbox"
    assert (await q(pool, "select opted_out from patient where id=%s", [world["p1"]]))[0]["opted_out"] is False


# ───────────────────────── idempotency, concurrency, parity ─────────────────────────
async def test_five_concurrent_confirm_taps_change_state_once(pool, world, messaging):
    a = await book(pool, world, "10:30")
    res = await asyncio.gather(*[tap(pool, messaging, "p1", f"CONFIRM:{a['id']}") for _ in range(5)])
    assert sorted(r["outcome"] for r in res) == ["already_confirmed"] * 4 + ["confirmed"]
    assert [e for e in await events(pool, a["id"]) if e[0] == "confirmed"] == [("confirmed", "patient")]
    assert len(await sent(pool, world)) == 1


async def test_racing_confirm_and_cancel_end_cancelled_consistently(pool, world, messaging):
    a = await book(pool, world, "10:30")
    await asyncio.gather(tap(pool, messaging, "p1", f"CONFIRM:{a['id']}"), tap(pool, messaging, "p1", f"CANCEL:{a['id']}"))
    ev = [t for t, _ in await events(pool, a["id"])]
    assert (await status_of(pool, a["id"]))["status"] == "cancelled"
    assert ev in (["pending", "cancelled"], ["pending", "confirmed", "cancelled"])  # never confirmed AFTER cancelled


async def test_actions_behave_identically_via_webhook_and_simulator_inbound(client, pool, world, messaging):
    h = token_for(world["recep_user"])
    a1 = await book(pool, world, "10:30", patient="p1")
    a2 = await book(pool, world, "10:30", patient="p2", doctor="arjun")
    r1 = await post_signed(client, mp.button_payload(PHONES["p1"], f"CONFIRM:{a1['id']}", "Confirm"))
    r2 = await client.post("/simulator/inbound", json=mp.button_payload(PHONES["p2"], f"CONFIRM:{a2['id']}", "Confirm"), headers=h)
    assert r1.status_code == 200 and r2.json()["results"][0]["outcome"] == "confirmed"
    assert (await status_of(pool, a1["id"]))["status"] == (await status_of(pool, a2["id"]))["status"] == "confirmed"
    t1, t2 = [(await sent(pool, world, p))[0][1] for p in ("p1", "p2")]
    assert t1 == t2                                                              # same words, same date, same time
    await post_signed(client, mp.text_payload(PHONES["p1"], "STOP"))
    await client.post("/simulator/inbound", json=mp.text_payload(PHONES["p2"], "STOP"), headers=h)
    rows = await q(pool, "select opted_out from patient where id = any(%s)", [[world["p1"], world["p2"]]])
    assert [r["opted_out"] for r in rows] == [True, True]
