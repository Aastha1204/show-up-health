"""HTTP-level tests: auth, validation, conflicts, slots, status machine. Need Postgres (marker: db)."""
from datetime import datetime

import pytest

from .conftest import at, token_for

pytestmark = pytest.mark.db


def payload(w, hhmm="10:00", tt="consult", doctor="neha", **extra):
    return {"patient_id": str(w["p1"]), "doctor_id": str(w[doctor]), "treatment_type_id": str(w[tt]),
            "start_at": at(w["day"], hhmm).isoformat(), **extra}


async def test_requires_authentication(client, world):
    assert (await client.get("/me")).status_code == 401
    assert (await client.get("/me", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_valid_token_without_staff_profile_is_403(client, world):
    r = await client.get("/me", headers=token_for(world["stranger"]))
    assert r.status_code == 403


async def test_role_comes_from_staff_profile(client, world):
    r = await client.get("/me", headers=token_for(world["recep_user"]))
    assert r.json()["role"] == "reception"
    r = await client.get("/me", headers=token_for(world["owner_user"]))
    assert r.json()["role"] == "owner"


async def test_create_and_list_in_ist(client, world):
    h = token_for(world["recep_user"])
    r = await client.post("/appointments", json=payload(world, "10:30"), headers=h)
    assert r.status_code == 201, r.text
    lst = await client.get("/appointments", params={"date": world["day"].isoformat()}, headers=h)
    rows = lst.json()
    assert len(rows) == 1 and rows[0]["patient_name"] == "Patient 1" and rows[0]["status"] == "pending"
    # 10:30 IST is 05:00 UTC
    assert rows[0]["start_at"].replace("Z", "+00:00").startswith(f"{world['day'].isoformat()}T05:00:00")


async def test_overlap_returns_409_with_code(client, world):
    h = token_for(world["recep_user"])
    assert (await client.post("/appointments", json=payload(world, "10:00", "rct"), headers=h)).status_code == 201
    r = await client.post("/appointments", json=payload(world, "10:30"), headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "slot_conflict"


async def test_outside_working_hours_is_422(client, world):
    h = token_for(world["recep_user"])
    for hhmm in ["09:00", "14:00", "13:20", "21:00"]:
        r = await client.post("/appointments", json=payload(world, hhmm), headers=h)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "outside_hours", hhmm


async def test_new_patient_requires_consent_source_and_valid_phone(client, world):
    h = token_for(world["recep_user"])
    base = {"doctor_id": str(world["neha"]), "treatment_type_id": str(world["consult"]),
            "start_at": at(world["day"], "10:00").isoformat()}
    no_consent = {**base, "new_patient": {"name": "Asha", "phone": "9876500099"}}
    assert (await client.post("/appointments", json=no_consent, headers=h)).status_code == 422
    bad_phone = {**base, "new_patient": {"name": "Asha", "phone": "12345", "consent_source": "reception"}}
    assert (await client.post("/appointments", json=bad_phone, headers=h)).status_code == 422
    ok = {**base, "new_patient": {"name": "Asha", "phone": "98765 00099", "consent_source": "reception"}}
    r = await client.post("/appointments", json=ok, headers=h)
    assert r.status_code == 201, r.text
    pts = (await client.get("/patients", params={"q": "Asha"}, headers=h)).json()
    assert pts[0]["phone"] == "+919876500099" and pts[0]["consent_source"] == "reception"


async def test_slots_endpoint_excludes_booked_time_and_reports_utc_instants(client, world):
    h = token_for(world["recep_user"])
    await client.post("/appointments", json=payload(world, "10:00", "rct"), headers=h)
    r = await client.get("/slots", headers=h, params={
        "doctor_id": str(world["neha"]), "date": world["day"].isoformat(), "treatment_type_id": str(world["consult"])})
    parse = lambda xs: {datetime.fromisoformat(x.replace("Z", "+00:00")) for x in xs}  # noqa: E731
    slots = parse(r.json()["slots"])
    assert r.json()["duration_min"] == 20
    assert at(world["day"], "10:00") not in slots
    assert at(world["day"], "10:45") not in slots
    assert at(world["day"], "11:00") in slots
    other = parse((await client.get("/slots", headers=h, params={
        "doctor_id": str(world["arjun"]), "date": world["day"].isoformat(), "duration_min": 20})).json()["slots"])
    assert at(world["day"], "10:00") in other


async def test_status_flow_is_idempotent_and_counts_no_shows(client, world):
    h = token_for(world["recep_user"])
    appt = (await client.post("/appointments", json=payload(world), headers=h)).json()
    url = f"/appointments/{appt['id']}/status"
    assert (await client.post(url, json={"status": "confirmed"}, headers=h)).json()["status"] == "confirmed"
    assert (await client.post(url, json={"status": "confirmed"}, headers=h)).status_code == 200  # repeat = no-op
    detail = (await client.get(f"/appointments/{appt['id']}", headers=h)).json()
    assert [e["to_status"] for e in detail["events"]] == ["pending", "confirmed"]  # no duplicate event
    assert (await client.post(url, json={"status": "no_show"}, headers=h)).json()["status"] == "no_show"
    assert (await client.post(url, json={"status": "confirmed"}, headers=h)).status_code == 422  # terminal
    pts = (await client.get("/patients", params={"q": "Patient 1"}, headers=h)).json()
    assert pts[0]["no_show_count"] == 1


async def test_patch_reschedules_and_conflicts(client, world):
    h = token_for(world["recep_user"])
    a = (await client.post("/appointments", json=payload(world, "10:00"), headers=h)).json()
    await client.post("/appointments", json={**payload(world, "11:00"), "patient_id": str(world["p2"])}, headers=h)
    ok = await client.patch(f"/appointments/{a['id']}", json={"start_at": at(world["day"], "12:00").isoformat()}, headers=h)
    assert ok.status_code == 200 and ok.json()["status"] == "rescheduled"
    bad = await client.patch(f"/appointments/{a['id']}", json={"start_at": at(world["day"], "11:00").isoformat()}, headers=h)
    assert bad.status_code == 409


async def test_unknown_appointment_is_404(client, world):
    h = token_for(world["recep_user"])
    r = await client.get("/appointments/00000000-0000-0000-0000-000000000000", headers=h)
    assert r.status_code == 404
