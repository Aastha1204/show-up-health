"""Presenter controls behind /demo."""
import asyncio
import os
from datetime import timedelta

import pytest

from app import seed
from app.services.slots import compute_slots
from app.timeutil import IST, ist_today, utcnow, weekday_key
from .conftest import token_for
from .test_messaging_db import q

pytestmark = pytest.mark.db


def auth(w):
    return token_for(w["recep_user"])


async def prospect(client, w, **over):
    body = {"name": "Priya Prospect", "phone": "98765 43210", "language": "en", **over}
    return await client.post("/demo/prospect", json=body, headers=auth(w))


# ───────────────────────── prospect ─────────────────────────
async def test_prospect_is_booked_tomorrow_at_the_earliest_free_slot_and_confirmed_immediately(client, pool, world):
    r = await prospect(client, world)
    assert r.status_code == 200, r.text
    j = r.json()
    start = __import__("datetime").datetime.fromisoformat(j["start_at"].replace("Z", "+00:00"))
    local = start.astimezone(IST)
    assert local.date() > ist_today() and (local.date() - ist_today()).days <= 3                  # tomorrow, or the next working day
    assert weekday_key(local.date()) != "sun" and local.strftime("%H:%M") == "10:00"            # empty clinic: first slot of the day
    assert j["doctor_name"] == "Dr. Arjun" and j["confirmation"] == "sent" and j["mode"] == "simulator"
    (msg,) = await q(pool, "select template_name, transport, status, rendered_body from outbound_message")
    assert (msg["template_name"], msg["transport"], msg["status"]) == ("showup_booking_confirmation", "simulator", "sent")
    assert msg["rendered_body"].startswith("Hi Priya, your appointment at Test Clinic is booked for ")
    (p,) = await q(pool, "select * from patient where phone='+919876543210'")
    assert (p["is_demo_seed"], p["consent_source"], p["opted_out"]) == (False, "reception", False)   # a real person, not a seed
    jobs_ = {(x["type"], x["status"]) for x in await q(pool, "select type, status from reminder_job")}
    assert ("confirmation", "sent") in jobs_ and ("day_before", "scheduled") in jobs_ and ("two_hour", "scheduled") in jobs_


async def test_second_prospect_gets_the_next_free_slot_and_same_phone_reuses_the_patient(client, pool, world):
    a = (await prospect(client, world)).json()
    b = (await prospect(client, world, name="Different Name")).json()
    assert a["patient_id"] == b["patient_id"]                                                      # same phone: same patient
    assert a["start_at"] != b["start_at"] or a["doctor_name"] != b["doctor_name"]                  # never double-booked
    c = (await prospect(client, world, phone="98765 00099", name="Third")).json()
    assert len({(x["start_at"], x["doctor_name"]) for x in (a, b, c)}) == 3
    assert (await q(pool, "select count(*) as n from patient where phone='+919876543210'"))[0]["n"] == 1


@pytest.mark.parametrize("phone", ["12345", "", "abcdefghij"])
async def test_prospect_with_a_bad_phone_is_refused(client, world, phone):
    assert (await prospect(client, world, phone=phone)).status_code == 422


async def test_prospect_requires_login(client, world):
    assert (await client.post("/demo/prospect", json={"name": "x", "phone": "98765 43210"})).status_code == 401


# ───────────────────────── state, cancel-seeded, summary ─────────────────────────
async def test_state_without_seed_data(client, world):
    j = (await client.get("/demo/state", headers=auth(world))).json()
    assert j == {"mode": "simulator", "live_ready": False, "waitlist_ttl_min": 15, "cancel_me": None}


async def test_cancel_seeded_offers_the_slot_to_the_waitlist_once(client, pool, dsn, world, messaging):
    ds = await asyncio.to_thread(seed.load, dsn)
    await q(pool, "update clinic set quiet_hours_start='00:00', quiet_hours_end='00:00' returning id")      # offers are proactive sends
    h = auth(world)
    st = (await client.get("/demo/state", headers=h)).json()
    first = next(p for p in ds["patients"] if p["id"] == ds["waitlist_entries"][0]["patient_id"])
    assert st["cancel_me"]["appointment_id"] == str(ds["cancel_me_id"]) and st["cancel_me"]["next_waitlist_patient"] == first["name"]
    r = await client.post("/demo/cancel-seeded", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["offered_to"] == first["name"] and j["offer_id"] and j["offer_expires_at"]
    assert (await q(pool, "select status from appointment where id=%s", [ds["cancel_me_id"]]))[0]["status"] == "cancelled"
    assert [m["rendered_body"][:30] for m in await q(pool, "select rendered_body from outbound_message where template_name='showup_waitlist_offer'")] == [
        f"Hi {first['name'].split()[0]}, a slot has opened a"]
    again = await client.post("/demo/cancel-seeded", headers=h)
    assert again.status_code == 404 and "Reset the demo" in again.json()["detail"]["message"]
    assert (await client.get("/demo/state", headers=h)).json()["cancel_me"] is None


async def test_summary_goes_to_the_doctors_chat_for_the_first_working_day(client, pool, world):
    r = await client.post("/demo/summary", headers=auth(world), json={"doctor_id": str(world["neha"])})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    (m,) = await q(pool, "select rendered_body, doctor_id, transport from outbound_message")
    assert m["doctor_id"] == world["neha"] and m["transport"] == "simulator" and m["rendered_body"].startswith("Good morning Dr. Neha. Today at ")
    assert "0 appointments, 0 confirmed, 0 not yet confirmed" in m["rendered_body"]
    assert (await client.post("/demo/summary", headers=auth(world), json={"doctor_id": "00000000-0000-0000-0000-000000000000"})).status_code == 404


# ───────────────────────── ttl, mode, reset ─────────────────────────
async def test_waitlist_ttl_quick_set(client, pool, world):
    assert (await client.put("/demo/ttl", headers=auth(world), json={"minutes": 2})).json() == {"waitlist_ttl_min": 2}
    assert (await q(pool, "select waitlist_offer_ttl_min as t from clinic"))[0]["t"] == 2
    for bad in (0, 61, -5):
        assert (await client.put("/demo/ttl", headers=auth(world), json={"minutes": bad})).status_code == 422


async def test_new_offers_use_the_quick_set_ttl(client, pool, dsn, world, messaging):
    await asyncio.to_thread(seed.load, dsn)
    await q(pool, "update clinic set quiet_hours_start='00:00', quiet_hours_end='00:00' returning id")
    await client.put("/demo/ttl", headers=auth(world), json={"minutes": 2})
    j = (await client.post("/demo/cancel-seeded", headers=auth(world))).json()
    left = __import__("datetime").datetime.fromisoformat(j["offer_expires_at"].replace("Z", "+00:00")) - utcnow()
    assert timedelta(seconds=100) < left <= timedelta(minutes=2)                                 # 2 minutes, not 15


async def test_mode_toggle_allows_the_simulator_fallback_but_refuses_an_unready_live(client, pool, world):
    h = auth(world)
    assert (await client.put("/demo/mode", headers=h, json={"mode": "simulator"})).json() == {"mode": "simulator"}
    live = await client.put("/demo/mode", headers=h, json={"mode": "live"})
    assert live.status_code == 409 and live.json()["detail"]["code"] == "live_unavailable"
    assert (await q(pool, "select messaging_mode as m from clinic"))[0]["m"] == "simulator"
    assert (await client.put("/demo/mode", headers=h, json={"mode": "pigeon"})).status_code == 422


async def test_reset_restores_the_seed_but_keeps_logins_and_the_messaging_mode(client, pool, dsn, world):
    assert os.environ["DATABASE_URL"] == os.environ["TEST_DATABASE_URL"]                          # reset can only touch the test DB
    await asyncio.to_thread(seed.load, dsn)
    h = auth(world)
    await client.post("/demo/cancel-seeded", headers=h)
    await prospect(client, world)
    await q(pool, "update clinic set messaging_mode='live' returning id")                          # e.g. the presenter was in Live mode
    r = await client.post("/demo/reset", headers=h)
    assert r.status_code == 200 and r.json() == {"patients": 500, "appointments": r.json()["appointments"], "mode": "live"}
    assert (await q(pool, "select count(*) as n from patient"))[0]["n"] == 500                     # the prospect is gone
    assert (await q(pool, "select count(*) as n from appointment where recovered_via_waitlist"))[0]["n"] == 18
    assert (await q(pool, "select messaging_mode as m from clinic"))[0]["m"] == "live"
    st = (await client.get("/demo/state", headers=h)).json()                                       # same token still works; demo is repeatable
    assert st["cancel_me"] is not None and st["mode"] == "live"
    assert (await q(pool, "select count(*) as n from staff_profile"))[0]["n"] == 2


async def test_prospect_is_never_booked_into_a_slot_a_waitlisted_patient_is_waiting_for(client, pool, dsn, world, messaging):
    """Otherwise rescheduling the prospect would trigger the waitlist offer early and use up the one meant for step 4."""
    ds = await asyncio.to_thread(seed.load, dsn)
    await q(pool, "update clinic set quiet_hours_start='00:00', quiet_hours_end='00:00' returning id")
    from app.services import waitlist as wl
    j = (await prospect(client, world)).json()
    start = __import__("datetime").datetime.fromisoformat(j["start_at"].replace("Z", "+00:00"))
    doc = (await q(pool, "select id from doctor where name=%s", [j["doctor_name"]]))[0]["id"]
    async with pool.connection() as conn:
        assert await wl.candidates(conn, ds["clinic"]["id"], doc, start) == []          # nobody is waiting for that slot
    # and the seeded demo still works afterwards: the cancel-me slot goes to its waitlist patient, not to someone already used
    r = await client.post("/demo/cancel-seeded", headers=auth(world))
    first = next(p for p in ds["patients"] if p["id"] == ds["waitlist_entries"][0]["patient_id"])
    assert r.json()["offered_to"] == first["name"]
