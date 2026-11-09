"""Waitlist engine (spec 7.4) against a real database."""
import asyncio
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

from app import jobs, seed
from app.messaging import meta_payloads as mp, webhook
from app.reminders import fmt_date, fmt_time
from app.services import appointments as svc, reschedule, waitlist
from app.timeutil import ist_to_utc, utcnow
from .conftest import at, token_for
from .test_messaging_db import book, q

pytestmark = pytest.mark.db

TAKEN = waitlist.TAKEN


@pytest.fixture
async def clinic_open_24h(pool, world):
    """Offers are proactive sends and the real clock may be inside quiet hours; neutralise them for these tests.
    (Quiet-hours behaviour has its own test, which passes an explicit `now`.)"""
    await q(pool, "update clinic set quiet_hours_start='00:00', quiet_hours_end='00:00' where id=%s returning id", [world["clinic"]])


async def new_patient(pool, w, n, *, opted_out=False):
    pid, phone = uuid.uuid4(), f"+91987651{n:04d}"
    await q(pool, """insert into patient (id, clinic_id, name, phone, consent_source, opted_out) values (%s,%s,%s,%s,'reception',%s)
                     returning id""", [pid, w["clinic"], f"Extra {n}", phone, opted_out])
    return pid, phone


async def entry(pool, w, patient_id, *, doctor="neha", window="morning", day=None, tt="consult", age=0):
    """age = minutes ago it joined the list (older = earlier in the queue)."""
    eid = uuid.uuid4()
    await q(pool, """insert into waitlist_entry (id, clinic_id, patient_id, doctor_id, treatment_type_id, preferred_date,
                     preferred_window, created_at) values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            [eid, w["clinic"], patient_id, w[doctor] if doctor else None, w[tt] if tt else None, day or w["day"], window,
             utcnow() - timedelta(minutes=age)])
    return eid


async def tap_phone(pool, messaging, phone, payload, text="Tap"):
    return (await webhook.handle_payload(pool, mp.button_payload(phone, payload, text), messaging))[0]


async def pending_offers(pool):
    return await q(pool, "select * from waitlist_offer where status='pending'")


async def entry_status(pool, eid):
    return (await q(pool, "select status from waitlist_entry where id=%s", [eid]))[0]["status"]


async def cancel_via_tap(pool, messaging, appt, phone="+919876500000"):
    return await tap_phone(pool, messaging, phone, f"CANCEL:{appt['id']}", "Cancel")


async def offered_to(pool):
    """Patient ids that were sent a waitlist offer, in order."""
    return [r["patient_id"] for r in await q(pool, "select patient_id from outbound_message where template_name='showup_waitlist_offer' order by created_at")]


# ───────────────────────── who gets the offer ─────────────────────────
async def test_cancel_offers_the_slot_to_the_first_eligible_patient_only(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")                        # p1's 10:30 with Dr. Neha
    other_day = world["day"] + timedelta(days=1)
    wrong_day, _ = await new_patient(pool, world, 1)
    evening, _ = await new_patient(pool, world, 2)
    other_doc, _ = await new_patient(pool, world, 3)
    opted_out, _ = await new_patient(pool, world, 4, opted_out=True)
    first, first_phone = await new_patient(pool, world, 5)
    later, _ = await new_patient(pool, world, 6)
    await entry(pool, world, wrong_day, day=other_day, age=60)
    await entry(pool, world, evening, window="evening", age=55)
    await entry(pool, world, other_doc, doctor="arjun", age=50)
    await entry(pool, world, opted_out, age=45)
    e_first = await entry(pool, world, first, age=40)
    e_later = await entry(pool, world, later, age=30)

    r = await cancel_via_tap(pool, messaging, a)
    assert r["outcome"] == "cancelled"
    (offer,) = await pending_offers(pool)
    assert await offered_to(pool) == [first]                    # oldest eligible, nobody else
    assert (offer["doctor_id"], offer["slot_start_at"], offer["duration_min"]) == (world["neha"], a["start_at"], 20)
    assert timedelta(minutes=14, seconds=50) < offer["expires_at"] - utcnow() <= timedelta(minutes=15)
    assert await entry_status(pool, e_first) == "offered" and await entry_status(pool, e_later) == "waiting"
    (msg,) = await q(pool, "select rendered_body, buttons, transport from outbound_message where template_name='showup_waitlist_offer'")
    assert msg["rendered_body"] == (f"Hi Extra, a slot has opened at Test Clinic on {fmt_date(a['start_at'])} at 10:30 AM with "
                                    "Dr. Neha. We are holding it for you for 15 minutes. Would you like it?")
    assert [b["payload"] for b in msg["buttons"]] == [f"WL_BOOK:{offer['id']}", f"WL_SKIP:{offer['id']}"]
    assert [b["text"] for b in msg["buttons"]] == ["Book", "Not now"] and msg["transport"] == "simulator"


async def test_any_doctor_and_any_window_entries_match(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "17:30")
    p, _ = await new_patient(pool, world, 1)
    await entry(pool, world, p, doctor=None, window="any")
    await cancel_via_tap(pool, messaging, a)
    assert await offered_to(pool) == [p]


async def test_window_boundaries(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "13:00")                        # still "morning" (< 14:00)
    m, _ = await new_patient(pool, world, 1)
    ev, _ = await new_patient(pool, world, 2)
    await entry(pool, world, ev, window="evening", age=20)
    await entry(pool, world, m, window="morning", age=10)
    await cancel_via_tap(pool, messaging, a)
    assert await offered_to(pool) == [m]


async def test_candidate_whose_treatment_does_not_fit_is_skipped(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")                        # 10:30-10:50, freed
    await book(pool, world, "11:00", patient="p2")              # someone holds 11:00: a 60-minute visit cannot fit
    long_pat, _ = await new_patient(pool, world, 1)
    short_pat, _ = await new_patient(pool, world, 2)
    await entry(pool, world, long_pat, tt="rct", age=30)        # needs 60 min: cannot fit
    await entry(pool, world, short_pat, tt="consult", age=10)
    await cancel_via_tap(pool, messaging, a)
    assert await offered_to(pool) == [short_pat]
    assert (await pending_offers(pool))[0]["duration_min"] == 20


async def test_patient_with_an_overlapping_appointment_is_skipped(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    busy, _ = await new_patient(pool, world, 1)
    free, _ = await new_patient(pool, world, 2)
    async with pool.connection() as conn:                      # busy already has 10:30 with the OTHER doctor
        await svc.create_appointment(conn, clinic_id=world["clinic"], patient_id=busy, doctor_id=world["arjun"],
                                     treatment_type_id=world["consult"], start_at=at(world["day"], "10:30"))
    await entry(pool, world, busy, doctor=None, window="any", age=20)
    await entry(pool, world, free, doctor=None, window="any", age=10)
    await cancel_via_tap(pool, messaging, a)
    assert await offered_to(pool) == [free]


# ───────────────────────── notice, quiet hours, one offer per slot ─────────────────────────
async def test_no_offer_unless_the_slot_starts_more_than_60_minutes_away(pool, world, messaging, clinic_open_24h):
    start = at(world["day"], "10:30")
    p, _ = await new_patient(pool, world, 1)
    await entry(pool, world, p)
    clinic = (await q(pool, "select * from clinic"))[0]
    async with pool.connection() as conn:
        assert await waitlist.offer_freed_slot(conn, messaging, clinic, world["neha"], start, 20, start - timedelta(minutes=60)) is None
        assert await waitlist.offer_freed_slot(conn, messaging, clinic, world["neha"], start, 20, start - timedelta(minutes=30)) is None
    assert await pending_offers(pool) == []
    async with pool.connection() as conn:
        assert await waitlist.offer_freed_slot(conn, messaging, clinic, world["neha"], start, 20, start - timedelta(minutes=61))


async def test_no_offers_during_quiet_hours(pool, world, messaging):
    p, _ = await new_patient(pool, world, 1)
    e = await entry(pool, world, p)
    clinic = (await q(pool, "select * from clinic"))[0]       # default quiet hours 21:00-08:00
    night = ist_to_utc(world["day"] - timedelta(days=1), time(22, 0))
    async with pool.connection() as conn:
        assert await waitlist.offer_freed_slot(conn, messaging, clinic, world["neha"], at(world["day"], "10:30"), 20, night) is None
    assert await pending_offers(pool) == [] and await entry_status(pool, e) == "waiting"


async def test_only_one_pending_offer_per_freed_slot_even_when_triggered_concurrently(pool, world, messaging, clinic_open_24h):
    start = at(world["day"], "10:30")
    for i in range(1, 4):
        p, _ = await new_patient(pool, world, i)
        await entry(pool, world, p, age=10 - i)
    clinic = (await q(pool, "select * from clinic"))[0]

    async def trigger():
        async with pool.connection() as conn:
            return await waitlist.offer_freed_slot(conn, messaging, clinic, world["neha"], start, 20, utcnow())
    results = await asyncio.gather(*[trigger() for _ in range(5)])
    assert sum(r is not None for r in results) == 1
    assert len(await pending_offers(pool)) == 1 and len(await offered_to(pool)) == 1
    assert (await q(pool, "select count(*) as n from waitlist_entry where status='offered'"))[0]["n"] == 1


async def test_offer_is_not_made_for_a_slot_that_is_not_free(pool, world, messaging, clinic_open_24h):
    await book(pool, world, "10:30")                           # still occupied
    p, _ = await new_patient(pool, world, 1)
    await entry(pool, world, p)
    clinic = (await q(pool, "select * from clinic"))[0]
    async with pool.connection() as conn:
        assert await waitlist.offer_freed_slot(conn, messaging, clinic, world["neha"], at(world["day"], "10:30"), 20, utcnow()) is None


# ───────────────────────── Not now / expiry: next in line ─────────────────────────
async def test_not_now_passes_the_slot_down_the_queue_without_repeating(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    people = [await new_patient(pool, world, i) for i in (1, 2, 3)]
    eids = [await entry(pool, world, pid, age=30 - i) for i, (pid, _) in enumerate(people)]
    await cancel_via_tap(pool, messaging, a)
    for n, (pid, phone) in enumerate(people):
        (offer,) = await pending_offers(pool)
        assert await offered_to(pool) == [p for p, _ in people[: n + 1]]
        r = await tap_phone(pool, messaging, phone, f"WL_SKIP:{offer['id']}", "Not now")
        assert r["outcome"] == "waitlist_declined"
        assert (await q(pool, "select status from waitlist_offer where id=%s", [offer["id"]]))[0]["status"] == "declined"
    assert await pending_offers(pool) == []                    # nobody left: the slot is just an open gap
    assert len(await offered_to(pool)) == 3                    # never offered twice to the same person
    assert [await entry_status(pool, e) for e in eids] == ["waiting"] * 3     # they stay on the list for other openings
    texts = await q(pool, "select rendered_body from outbound_message where kind='text' and rendered_body=%s", [waitlist.DECLINED])
    assert len(texts) == 3


async def test_not_now_twice_is_a_silent_noop(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    pid, phone = await new_patient(pool, world, 1)
    await entry(pool, world, pid)
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    await tap_phone(pool, messaging, phone, f"WL_SKIP:{offer['id']}")
    again = await tap_phone(pool, messaging, phone, f"WL_SKIP:{offer['id']}")
    assert again["outcome"] == "waitlist_already_declined"
    assert len(await q(pool, "select 1 from outbound_message where rendered_body=%s", [waitlist.DECLINED])) == 1


async def test_expiry_in_the_tick_offers_to_the_next_candidate(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    (p1, _), (p2, _) = await new_patient(pool, world, 1), await new_patient(pool, world, 2)
    e1, e2 = await entry(pool, world, p1, age=20), await entry(pool, world, p2, age=10)
    await cancel_via_tap(pool, messaging, a)
    (first,) = await pending_offers(pool)
    await jobs.tick(pool, messaging)                            # not expired yet: nothing changes
    assert (await pending_offers(pool))[0]["id"] == first["id"]
    await q(pool, "update waitlist_offer set expires_at = now() - interval '1 minute' returning id")
    counts = await jobs.tick(pool, messaging)
    assert counts["offers_expired"] == 1
    assert (await q(pool, "select status from waitlist_offer where id=%s", [first["id"]]))[0]["status"] == "expired"
    assert await entry_status(pool, e1) == "waiting" and await entry_status(pool, e2) == "offered"
    assert await offered_to(pool) == [p1, p2]


async def test_concurrent_ticks_expire_an_offer_and_make_exactly_one_new_offer(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    for i in (1, 2, 3):
        p, _ = await new_patient(pool, world, i)
        await entry(pool, world, p, age=30 - i)
    await cancel_via_tap(pool, messaging, a)
    await q(pool, "update waitlist_offer set expires_at = now() - interval '1 minute' returning id")
    await asyncio.gather(*[jobs.tick(pool, messaging) for _ in range(3)])
    assert len(await pending_offers(pool)) == 1 and len(await offered_to(pool)) == 2


async def test_expiry_does_not_pass_on_a_slot_that_is_now_too_close(pool, world, messaging, clinic_open_24h):
    p1, _ = await new_patient(pool, world, 1)
    p2, _ = await new_patient(pool, world, 2)
    e1 = await entry(pool, world, p1, age=20)
    await entry(pool, world, p2, age=10)
    soon = utcnow() + timedelta(minutes=30)
    await q(pool, """insert into waitlist_offer (clinic_id, waitlist_entry_id, doctor_id, slot_start_at, duration_min, status, expires_at)
                     values (%s,%s,%s,%s,20,'pending', now() - interval '1 minute') returning id""", [world["clinic"], e1, world["neha"], soon])
    await q(pool, "update waitlist_entry set status='offered' where id=%s returning id", [e1])
    counts = await jobs.tick(pool, messaging)
    assert counts["offers_expired"] == 1 and await pending_offers(pool) == [] and await offered_to(pool) == []
    assert await entry_status(pool, e1) == "waiting"


# ───────────────────────── Book ─────────────────────────
async def test_book_creates_a_recovered_confirmed_appointment_and_confirms(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    pid, phone = await new_patient(pool, world, 1)
    eid = await entry(pool, world, pid)
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    r = await tap_phone(pool, messaging, phone, f"WL_BOOK:{offer['id']}", "Book")
    assert r["outcome"] == "waitlist_booked"
    (new,) = await q(pool, "select * from appointment where patient_id=%s", [pid])
    assert (new["source"], new["recovered_via_waitlist"], new["status"]) == ("waitlist", True, "confirmed")
    assert (new["doctor_id"], new["start_at"], new["duration_min"]) == (world["neha"], a["start_at"], 20)
    assert await entry_status(pool, eid) == "booked"
    assert (await q(pool, "select status from waitlist_offer where id=%s", [offer["id"]]))[0]["status"] == "accepted"
    last = (await q(pool, "select template_name, rendered_body from outbound_message where patient_id=%s order by created_at desc limit 1", [pid]))[0]
    assert last["template_name"] == "showup_booking_confirmation"
    assert f"booked for {fmt_date(a['start_at'])} at 10:30 AM with Dr. Neha" in last["rendered_body"]
    jobs_ = {(j["type"], j["status"]) for j in await q(pool, "select type, status from reminder_job where appointment_id=%s", [new["id"]])}
    assert jobs_ == {("confirmation", "sent"), ("two_hour", "scheduled")}      # confirmed already: no day_before
    ev = await q(pool, "select to_status, actor from appointment_event where appointment_id=%s", [new["id"]])
    assert [(e["to_status"], e["actor"]) for e in ev] == [("confirmed", "patient")]
    assert (await q(pool, "select count(*) as n from appointment where recovered_via_waitlist"))[0]["n"] == 1


async def test_repeat_book_tap_is_a_silent_noop(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    pid, phone = await new_patient(pool, world, 1)
    await entry(pool, world, pid)
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    await tap_phone(pool, messaging, phone, f"WL_BOOK:{offer['id']}")
    n_msgs = (await q(pool, "select count(*) as n from outbound_message where patient_id=%s", [pid]))[0]["n"]
    again = await tap_phone(pool, messaging, phone, f"WL_BOOK:{offer['id']}")
    assert again["outcome"] == "waitlist_already_accepted"
    assert (await q(pool, "select count(*) as n from appointment where patient_id=%s", [pid]))[0]["n"] == 1
    assert (await q(pool, "select count(*) as n from outbound_message where patient_id=%s", [pid]))[0]["n"] == n_msgs


async def test_five_concurrent_book_taps_book_exactly_once(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    pid, phone = await new_patient(pool, world, 1)
    await entry(pool, world, pid)
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    res = await asyncio.gather(*[tap_phone(pool, messaging, phone, f"WL_BOOK:{offer['id']}") for _ in range(5)])
    assert sorted(r["outcome"] for r in res) == ["waitlist_already_accepted"] * 4 + ["waitlist_booked"]
    assert (await q(pool, "select count(*) as n from appointment where patient_id=%s", [pid]))[0]["n"] == 1


async def test_book_after_expiry_is_declined_politely(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    (p1, ph1), (p2, _) = await new_patient(pool, world, 1), await new_patient(pool, world, 2)
    e1, _e2 = await entry(pool, world, p1, age=20), await entry(pool, world, p2, age=10)
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    await q(pool, "update waitlist_offer set expires_at = now() - interval '1 second' returning id")   # lapsed, tick not run yet
    r = await tap_phone(pool, messaging, ph1, f"WL_BOOK:{offer['id']}", "Book")
    assert r["outcome"] == "waitlist_expired"
    assert (await q(pool, "select count(*) as n from appointment where patient_id=%s", [p1]))[0]["n"] == 0
    assert (await q(pool, "select status from waitlist_offer where id=%s", [offer["id"]]))[0]["status"] == "expired"
    assert (await q(pool, "select rendered_body from outbound_message where kind='text' and patient_id=%s", [p1]))[0]["rendered_body"] == waitlist.EXPIRED
    assert await entry_status(pool, e1) == "waiting"
    assert await offered_to(pool) == [p1, p2]                  # and the slot moved on to the next person


async def test_book_when_the_slot_was_taken_meanwhile_apologises(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    pid, phone = await new_patient(pool, world, 1)
    eid = await entry(pool, world, pid)
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    async with pool.connection() as conn:                       # reception books the freed slot by hand
        await svc.create_appointment(conn, clinic_id=world["clinic"], patient_id=world["p3"], doctor_id=world["neha"],
                                     treatment_type_id=world["consult"], start_at=a["start_at"])
    r = await tap_phone(pool, messaging, phone, f"WL_BOOK:{offer['id']}", "Book")
    assert r["outcome"] == "waitlist_slot_taken"
    assert (await q(pool, "select status from waitlist_offer where id=%s", [offer["id"]]))[0]["status"] == "slot_taken"
    assert (await q(pool, "select count(*) as n from appointment where patient_id=%s", [pid]))[0]["n"] == 0
    assert await entry_status(pool, eid) == "waiting"
    assert (await q(pool, "select rendered_body from outbound_message where kind='text' and patient_id=%s", [pid]))[0]["rendered_body"] == TAKEN


async def test_someone_elses_offer_cannot_be_booked_or_skipped(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    pid, _ = await new_patient(pool, world, 1)
    _, intruder_phone = await new_patient(pool, world, 2)
    await entry(pool, world, pid)
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    for verb in ("WL_BOOK", "WL_SKIP"):
        assert (await tap_phone(pool, messaging, intruder_phone, f"{verb}:{offer['id']}"))["outcome"] == "waitlist_unavailable"
    assert len(await pending_offers(pool)) == 1
    assert (await q(pool, "select count(*) as n from appointment where recovered_via_waitlist"))[0]["n"] == 0
    assert (await tap_phone(pool, messaging, intruder_phone, "WL_BOOK:not-a-uuid"))["outcome"] == "waitlist_unavailable"


async def test_opting_out_while_holding_an_offer_blocks_the_next_offer_not_the_state(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    (p1, ph1), (p2, _) = await new_patient(pool, world, 1), await new_patient(pool, world, 2)
    await entry(pool, world, p1, age=20)
    await entry(pool, world, p2, age=10)
    await q(pool, "update patient set opted_out=true where id=%s returning id", [p2])   # p2 STOPs before it is their turn
    await cancel_via_tap(pool, messaging, a)
    (offer,) = await pending_offers(pool)
    await tap_phone(pool, messaging, ph1, f"WL_SKIP:{offer['id']}")
    assert await offered_to(pool) == [p1] and await pending_offers(pool) == []


# ───────────────────────── reschedule-away feeds the waitlist ─────────────────────────
async def test_rescheduling_away_offers_the_old_slot(pool, world, messaging, clinic_open_24h):
    a = await book(pool, world, "10:30")
    pid, _ = await new_patient(pool, world, 1)
    await entry(pool, world, pid)
    await tap_phone(pool, messaging, "+919876500000", f"RESCHEDULE:{a['id']}", "Reschedule")
    lst = (await q(pool, "select list from outbound_message where kind='interactive_list'"))[0]["list"]
    row = lst["sections"][0]["rows"][0]
    r = (await webhook.handle_payload(pool, mp.list_reply_payload("+919876500000", row["id"], row["title"]), messaging))[0]
    assert r["outcome"] == "rescheduled"
    (offer,) = await pending_offers(pool)
    assert (offer["doctor_id"], offer["slot_start_at"]) == (world["neha"], a["start_at"])
    assert await offered_to(pool) == [pid]


# ───────────────────────── API ─────────────────────────
async def test_waitlist_api_add_list_and_remove(client, pool, world, messaging, clinic_open_24h):
    h = token_for(world["recep_user"])
    day = world["day"].isoformat()
    r = await client.post("/waitlist", headers=h, json={"new_patient": {"name": "Wait Er", "phone": "98765 11111", "consent_source": "reception"},
                                                        "doctor_id": None, "treatment_type_id": str(world["consult"]),
                                                        "preferred_date": day, "preferred_window": "morning"})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    bad = await client.post("/waitlist", headers=h, json={"preferred_date": day})
    assert bad.status_code == 422                                  # a patient is required
    rows = (await client.get("/waitlist", headers=h, params={"date": day})).json()
    assert [(x["patient_name"], x["doctor_name"], x["preferred_window"], x["status"], x["offer_id"]) for x in rows] == [
        ("Wait Er", None, "morning", "waiting", None)]
    assert (await client.delete(f"/waitlist/{eid}", headers=h)).status_code == 200
    assert (await client.delete(f"/waitlist/{eid}", headers=h)).status_code == 404         # already closed
    assert (await client.get("/waitlist", headers=h, params={"date": day})).json() == []
    closed = (await client.get("/waitlist", headers=h, params={"date": day, "include_closed": "true"})).json()
    assert closed[0]["status"] == "removed"


async def test_active_offer_is_exposed_with_its_expiry_for_the_countdown(client, pool, world, messaging, clinic_open_24h):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    pid, _ = await new_patient(pool, world, 1)
    await entry(pool, world, pid)
    await cancel_via_tap(pool, messaging, a)
    (row,) = (await client.get("/waitlist", headers=h, params={"date": world["day"].isoformat()})).json()
    assert row["status"] == "offered" and row["offer_id"] and row["offer_expires_at"] and row["offer_slot_start_at"]


async def test_removing_an_entry_that_holds_the_offer_moves_it_on(client, pool, world, messaging, clinic_open_24h):
    h = token_for(world["recep_user"])
    a = await book(pool, world, "10:30")
    (p1, _), (p2, _) = await new_patient(pool, world, 1), await new_patient(pool, world, 2)
    e1, _e2 = await entry(pool, world, p1, age=20), await entry(pool, world, p2, age=10)
    await cancel_via_tap(pool, messaging, a)
    assert (await client.delete(f"/waitlist/{e1}", headers=h)).status_code == 200
    (offer,) = await pending_offers(pool)
    assert await offered_to(pool) == [p1, p2]
    assert (await q(pool, "select patient_id from waitlist_entry where id=%s", [offer["waitlist_entry_id"]]))[0]["patient_id"] == p2


async def test_waitlist_api_requires_login(client, world):
    assert (await client.get("/waitlist")).status_code == 401
    assert (await client.delete(f"/waitlist/{uuid.uuid4()}")).status_code == 401


# ───────────────────────── the demo's headline flow, on the real seed ─────────────────────────
async def test_seeded_demo_cancel_offer_book_recovers_one_more_slot(pool, dsn, world, messaging):
    ds = await asyncio.to_thread(seed.load, dsn)
    await q(pool, "update clinic set quiet_hours_start='00:00', quiet_hours_end='00:00' returning id")
    recovered = lambda: q(pool, "select count(*) as n from appointment where recovered_via_waitlist")  # noqa: E731
    assert (await recovered())[0]["n"] == 18                                  # exactly 18 recovered after go-live

    cancel_me = next(a for a in ds["appointments"] if a["id"] == ds["cancel_me_id"])
    owner = next(p for p in ds["patients"] if p["id"] == cancel_me["patient_id"])
    first_entry = ds["waitlist_entries"][0]                                   # the entry seeded to match this slot
    waiter = next(p for p in ds["patients"] if p["id"] == first_entry["patient_id"])

    r = await tap_phone(pool, messaging, owner["phone"], f"CANCEL:{cancel_me['id']}", "Cancel")    # reception/patient cancels
    assert r["outcome"] == "cancelled"
    (offer,) = await pending_offers(pool)                                     # the offer goes out...
    assert offer["waitlist_entry_id"] == first_entry["id"] and offer["slot_start_at"] == cancel_me["start_at"]
    assert await offered_to(pool) == [waiter["id"]]                           # ...to the waitlisted patient's chat

    r = await tap_phone(pool, messaging, waiter["phone"], f"WL_BOOK:{offer['id']}", "Book")        # they tap Book
    assert r["outcome"] == "waitlist_booked"
    (filled,) = await q(pool, "select status, source, start_at from appointment where patient_id=%s and recovered_via_waitlist", [waiter["id"]])
    assert (filled["status"], filled["source"], filled["start_at"]) == ("confirmed", "waitlist", cancel_me["start_at"])   # gap filled
    assert (await recovered())[0]["n"] == 19                                  # 18 -> 19
