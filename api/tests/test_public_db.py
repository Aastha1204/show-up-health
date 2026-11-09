"""Public booking endpoints: the website form's backend. Unauthenticated, so the abuse cases matter most."""
import uuid
from datetime import timedelta

import pytest

from app.ratelimit import RateLimiter
from app.timeutil import utcnow
from .conftest import at
from .test_messaging_db import book, q

pytestmark = pytest.mark.db

OK_ORIGIN = {"Origin": "http://localhost:3100"}
BASE = (3, 0)          # the world fixture starts with 3 patients and no appointments


def payload(w, hhmm="10:30", **over):
    p = {"name": "Meera Joshi", "phone": "98765 43210", "language": "en", "doctor_id": str(w["neha"]),
         "treatment_type_id": str(w["consult"]), "start_at": at(w["day"], hhmm).isoformat(), "consent": True}
    p.update(over)
    return p


async def counts(pool):
    return ((await q(pool, "select count(*) as n from patient"))[0]["n"], (await q(pool, "select count(*) as n from appointment"))[0]["n"])


# ───────────────────────── read-only helpers ─────────────────────────
async def test_clinic_info_has_what_the_form_needs_and_nothing_personal(client, world):
    j = (await client.get("/public/clinic")).json()
    assert j["name"] == "Test Clinic" and j["consent_text"] == "I agree to receive appointment updates on WhatsApp."
    assert sorted(d["name"] for d in j["doctors"]) == ["Dr. Arjun", "Dr. Neha"]
    assert {t["name"] for t in j["treatments"]} == {"Consultation", "Root canal session"}
    assert set(j["doctors"][0]) == {"id", "name"} and "phone" not in str(j).lower()


async def test_public_slots_hide_taken_times_and_union_doctors_when_none_chosen(client, pool, world):
    params = {"date": world["day"].isoformat(), "treatment_type_id": str(world["consult"])}
    await book(pool, world, "10:30", doctor="neha")
    neha = (await client.get("/public/slots", params={**params, "doctor_id": str(world["neha"])})).json()["slots"]
    anyone = (await client.get("/public/slots", params=params)).json()["slots"]
    t1030 = at(world["day"], "10:30").isoformat().replace("+00:00", "Z")
    norm = lambda xs: {x.replace("Z", "+00:00") for x in xs}  # noqa: E731
    assert at(world["day"], "10:30").isoformat() not in norm(neha)             # Neha is busy then
    assert at(world["day"], "10:30").isoformat() in norm(anyone)               # but Dr. Arjun is free: so "any doctor" offers it


async def test_public_slots_outside_the_booking_window_are_empty(client, world):
    for d in (utcnow().date() - timedelta(days=2), utcnow().date() + timedelta(days=90)):
        r = await client.get("/public/slots", params={"date": d.isoformat(), "treatment_type_id": str(world["consult"])})
        assert r.json()["slots"] == []


# ───────────────────────── booking ─────────────────────────
async def test_booking_creates_a_consented_patient_and_a_website_appointment(client, pool, world):
    r = await client.post("/public/appointments", json=payload(world), headers=OK_ORIGIN)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j == {"ok": True, "start_at": j["start_at"], "doctor_name": "Dr. Neha", "clinic_name": "Test Clinic"}   # no ids, no PII
    (p,) = await q(pool, "select * from patient where phone='+919876543210'")
    assert (p["name"], p["consent_source"], p["language"], p["opted_out"], p["is_demo_seed"]) == ("Meera Joshi", "website_form", "en", False, False)
    assert p["consent_at"] is not None
    (a,) = await q(pool, "select * from appointment where patient_id=%s", [p["id"]])
    assert (a["source"], a["status"], a["start_at"]) == ("website", "pending", at(world["day"], "10:30"))
    types = {j["type"] for j in await q(pool, "select type from reminder_job where appointment_id=%s", [a["id"]])}
    assert {"confirmation", "day_before", "two_hour"} <= types                  # the confirmation WhatsApp is on its way


async def test_any_doctor_picks_a_free_one_and_reports_when_none_is(client, pool, world):
    first = await client.post("/public/appointments", json=payload(world, doctor_id=None), headers=OK_ORIGIN)
    assert first.json()["doctor_name"] == "Dr. Arjun"                           # alphabetical first free doctor
    second = await client.post("/public/appointments", json=payload(world, doctor_id=None, name="Other Person", phone="98765 00001"), headers=OK_ORIGIN)
    assert second.json()["doctor_name"] == "Dr. Neha"                           # Arjun now busy at 10:30
    third = await client.post("/public/appointments", json=payload(world, doctor_id=None, name="Third", phone="98765 00002"), headers=OK_ORIGIN)
    assert third.status_code == 409 and third.json()["detail"]["code"] == "slot_conflict"


async def test_a_slot_that_was_just_taken_gets_a_friendly_409(client, pool, world):
    await book(pool, world, "10:30")
    r = await client.post("/public/appointments", json=payload(world), headers=OK_ORIGIN)
    assert r.status_code == 409 and "just taken" in r.json()["detail"]["message"]
    assert (await counts(pool))[1] == 1


async def test_consent_is_mandatory(client, pool, world):
    before = await counts(pool)
    for bad in (payload(world, consent=False), {k: v for k, v in payload(world).items() if k != "consent"}):
        r = await client.post("/public/appointments", json=bad, headers=OK_ORIGIN)
        assert r.status_code == 422
    assert "I agree to receive appointment updates on WhatsApp." in (await client.post(
        "/public/appointments", json=payload(world, consent=False), headers=OK_ORIGIN)).text
    assert await counts(pool) == before


@pytest.mark.parametrize("over", [{"phone": "12345"}, {"phone": ""}, {"name": "   "}, {"name": ""}, {"language": "fr"}])
async def test_invalid_details_are_rejected(client, pool, world, over):
    r = await client.post("/public/appointments", json=payload(world, **over), headers=OK_ORIGIN)
    assert r.status_code == 422 and await counts(pool) == BASE


@pytest.mark.parametrize("when_", ["past", "soon", "far"])
async def test_times_outside_the_booking_window_are_rejected(client, pool, world, when_):
    start = {"past": utcnow() - timedelta(days=1), "soon": utcnow() + timedelta(minutes=30), "far": utcnow() + timedelta(days=200)}[when_]
    r = await client.post("/public/appointments", json=payload(world, start_at=start.isoformat()), headers=OK_ORIGIN)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_time" and await counts(pool) == BASE


async def test_a_time_outside_working_hours_is_rejected(client, pool, world):
    r = await client.post("/public/appointments", json=payload(world, "14:00"), headers=OK_ORIGIN)
    assert r.status_code == 422 and "not available" in r.json()["detail"]["message"]


# ───────────────────────── abuse ─────────────────────────
async def test_honeypot_looks_successful_but_creates_nothing(client, pool, world):
    r = await client.post("/public/appointments", json=payload(world, website="http://spam.example"), headers=OK_ORIGIN)
    assert r.status_code == 201 and r.json() == {"ok": True}                   # the bot is none the wiser
    assert await counts(pool) == BASE


async def test_rate_limit_stops_a_script_and_cannot_be_dodged_by_a_spoofed_forwarded_header(client, pool, world):
    codes = []
    for i in range(7):                                                          # honeypot hits: cheap, still counted
        r = await client.post("/public/appointments", json=payload(world, website="x"), headers={**OK_ORIGIN, "X-Forwarded-For": f"10.0.0.{i}"})
        codes.append(r.status_code)
    assert codes == [201] * 5 + [429] * 2
    assert int(r.headers["retry-after"]) >= 1 and r.json()["detail"]["code"] == "rate_limited"


def test_rate_limiter_windows():
    rl = RateLimiter([(2, 10), (3, 100)])
    assert rl.check("a", now=0) is None and rl.check("a", now=1) is None
    assert rl.check("a", now=2) == 9                                            # 2 per 10 s: wait for the first to age out
    assert rl.check("b", now=2) is None                                         # another address is unaffected
    assert rl.check("a", now=11) is None                                        # 10 s later one slot is free again
    assert rl.check("a", now=22) is not None                                    # but 3 per 100 s is now used up
    assert rl.check("a", now=200) is None                                       # ...and ages out too


async def test_origin_allow_list(client, pool, world):
    evil = {"Origin": "https://evil.example"}
    assert (await client.get("/public/clinic", headers=evil)).status_code == 403
    assert (await client.get("/public/slots", headers=evil, params={"date": world["day"].isoformat(), "treatment_type_id": str(world["consult"])})).status_code == 403
    assert (await client.post("/public/appointments", json=payload(world), headers=evil)).status_code == 403
    assert await counts(pool) == BASE
    for ok in (OK_ORIGIN, {"Origin": "https://smilecare-clinic.example"}, {}):   # a listed site, the booking page itself, a plain client
        assert (await client.get("/public/clinic", headers=ok)).status_code == 200


async def test_cors_preflight_is_granted_only_to_listed_sites(client):
    ask = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    good = await client.options("/public/appointments", headers={"Origin": "https://smilecare-clinic.example", **ask})
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == "https://smilecare-clinic.example"
    bad = await client.options("/public/appointments", headers={"Origin": "https://evil.example", **ask})
    assert "access-control-allow-origin" not in bad.headers


async def test_responses_do_not_reveal_whether_a_number_is_already_a_patient_or_opted_out(client, pool, world):
    fresh = await client.post("/public/appointments", json=payload(world, "10:30", phone="98765 10001"), headers=OK_ORIGIN)
    await q(pool, "insert into patient (clinic_id, name, phone, consent_source, opted_out) values (%s,'Known','+919876510002','reception',true) returning id", [world["clinic"]])
    known_opted_out = await client.post("/public/appointments", json=payload(world, "11:00", phone="98765 10002"), headers=OK_ORIGIN)
    assert fresh.status_code == known_opted_out.status_code == 201
    assert set(fresh.json()) == set(known_opted_out.json())                    # same shape: nothing to enumerate
    (p,) = await q(pool, "select name, opted_out, count(*) over () as n from patient where phone='+919876510002'")
    assert p["opted_out"] is True and p["name"] == "Known"                     # a web form never silently re-subscribes anyone


async def test_booking_needs_no_login(client, world):
    r = await client.post("/public/appointments", json=payload(world), headers=OK_ORIGIN)       # no Authorization header
    assert r.status_code == 201
