"""Running-late broadcast (spec 7.5)."""
import uuid
from datetime import time, timedelta

import pytest

from app.services import appointments as svc, broadcast
from app.timeutil import ist_to_utc
from .conftest import at, token_for
from .test_messaging_db import q

pytestmark = pytest.mark.db

BODY = ("Hi Patient, Dr. Neha is running about 30 minutes late today. Your {t} appointment may start a little later. "
        "We are sorry for the wait.")


def fake_now(w):
    return ist_to_utc(w["day"], time(9, 0))          # 09:00 IST on the (future) test Monday; not quiet hours


async def person(pool, w, n, *, opted_out=False):
    pid = uuid.uuid4()
    await q(pool, """insert into patient (id, clinic_id, name, phone, consent_source, opted_out) values (%s,%s,%s,%s,'reception',%s)
                     returning id""", [pid, w["clinic"], f"Patient {n}", f"+91987652{n:04d}", opted_out])
    return pid


async def appt(pool, w, patient, hhmm, *, doctor="neha", status="pending", day_offset=0, tt="consult"):
    async with pool.connection() as conn:
        a = await svc.create_appointment(conn, clinic_id=w["clinic"], patient_id=patient, doctor_id=w[doctor],
                                         treatment_type_id=w[tt], start_at=at(w["day"] + timedelta(days=day_offset), hhmm),
                                         enforce_hours=False, schedule_reminders=False, status=status)
    return a


@pytest.fixture
async def scenario(pool, world):
    """Dr. Neha, 'now' = 09:00 on the test Monday. Eligible: p_a (10:30), p_b (11:00), p_f (17:00 rescheduled)."""
    w = world
    pa, pb, pc, pd, pe, pf, pg, ph = [await person(pool, w, n) for n in range(1, 9)]
    out = await person(pool, w, 9, opted_out=True)
    await appt(pool, w, pa, "10:30")                                            # ok
    await appt(pool, w, pa, "17:30")                                            # same patient later: ONE message, earliest time
    await appt(pool, w, pb, "11:00", status="confirmed")                         # ok
    await appt(pool, w, pf, "17:00", status="rescheduled")                       # ok
    await appt(pool, w, pc, "08:30")                                            # already started: excluded
    await appt(pool, w, pd, "12:00", status="arrived")                          # excluded
    await appt(pool, w, pe, "12:30", status="cancelled")                         # excluded
    await appt(pool, w, pg, "10:30", doctor="arjun")                            # other doctor: excluded
    await appt(pool, w, ph, "10:30", day_offset=1)                              # tomorrow: excluded
    await appt(pool, w, out, "13:00")                                           # opted out: excluded
    return {"a": pa, "b": pb, "f": pf}


async def test_recipients_are_this_doctors_remaining_active_appointments_today(pool, world, scenario):
    async with pool.connection() as conn:
        rec = await broadcast.recipients(conn, world["clinic"], world["neha"], fake_now(world))
    assert [(r["patient_id"], r["start_at"]) for r in rec] == [
        (scenario["a"], at(world["day"], "10:30")), (scenario["b"], at(world["day"], "11:00")),
        (scenario["f"], at(world["day"], "17:00"))]


async def test_preview_shows_the_exact_message_and_count(pool, world, scenario):
    async with pool.connection() as conn:
        pv = await broadcast.preview(conn, world["clinic"], world["neha"], 30, fake_now(world))
    assert pv["recipient_count"] == 3 and pv["delay_min"] == 30 and pv["doctor_name"] == "Dr. Neha"
    assert pv["preview"] == BODY.format(t="10:30 AM")
    assert [r["name"] for r in pv["recipients"]] == ["Patient 1", "Patient 2", "Patient 6"]


async def test_preview_with_nobody_still_shows_a_sample(pool, world):
    async with pool.connection() as conn:
        pv = await broadcast.preview(conn, world["clinic"], world["arjun"], 15, fake_now(world))
    assert pv["recipient_count"] == 0 and "running about 15 minutes late" in pv["preview"]


async def test_send_messages_each_patient_once_with_their_own_time_and_logs_a_broadcast(pool, world, messaging, scenario):
    staff = world["recep_user"]
    r = await broadcast.send_running_late(pool, messaging, clinic_id=world["clinic"], doctor_id=world["neha"], delay_min=30,
                                          created_by=staff, now=fake_now(world))
    assert (r["targeted"], r["sent"], r["failed"], r["skipped"]) == (3, 3, 0, {})
    rows = await q(pool, """select p.name, m.rendered_body, m.template_name, m.transport, m.status, m.appointment_id from outbound_message m
                            join patient p on p.id = m.patient_id order by p.name""")
    assert [x["name"] for x in rows] == ["Patient 1", "Patient 2", "Patient 6"]            # one each, nobody else
    assert [x["rendered_body"] for x in rows] == [BODY.format(t="10:30 AM"), BODY.format(t="11:00 AM"), BODY.format(t="5:00 PM")]
    assert all(x["template_name"] == "showup_running_late" and x["transport"] == "simulator" and x["status"] == "sent" for x in rows)
    (b,) = await q(pool, "select * from broadcast")
    assert (b["id"], b["doctor_id"], b["delay_min"], b["recipient_count"], b["created_by"]) == (r["broadcast_id"], world["neha"], 30, 3, staff)


async def test_double_click_is_refused_but_a_different_delay_is_allowed(pool, world, messaging, scenario):
    kw = dict(clinic_id=world["clinic"], doctor_id=world["neha"], created_by=None, now=fake_now(world))
    await broadcast.send_running_late(pool, messaging, delay_min=30, **kw)
    with pytest.raises(svc.Conflict) as e:
        await broadcast.send_running_late(pool, messaging, delay_min=30, **kw)
    assert e.value.code == "duplicate_broadcast"
    assert (await q(pool, "select count(*) as n from outbound_message"))[0]["n"] == 3     # the second click sent nothing
    again = await broadcast.send_running_late(pool, messaging, delay_min=45, **kw)
    assert again["sent"] == 3


@pytest.mark.parametrize("delay", [0, 10, 20, 90, -15])
async def test_only_15_30_45_60_are_valid_delays(pool, world, messaging, delay):
    with pytest.raises(svc.Invalid):
        await broadcast.send_running_late(pool, messaging, clinic_id=world["clinic"], doctor_id=world["neha"], delay_min=delay,
                                          created_by=None, now=fake_now(world))


async def test_no_recipients_sends_nothing_and_logs_nothing(pool, world, messaging):
    r = await broadcast.send_running_late(pool, messaging, clinic_id=world["clinic"], doctor_id=world["arjun"], delay_min=15,
                                          created_by=None, now=fake_now(world))
    assert r == {"broadcast_id": None, "targeted": 0, "sent": 0, "skipped": {}, "failed": 0}
    assert (await q(pool, "select count(*) as n from broadcast"))[0]["n"] == 0


async def test_unknown_doctor_is_404(pool, world, messaging):
    with pytest.raises(svc.NotFound):
        await broadcast.send_running_late(pool, messaging, clinic_id=world["clinic"], doctor_id=uuid.uuid4(), delay_min=15,
                                          created_by=None, now=fake_now(world))


async def test_a_message_that_cannot_be_sent_is_counted_not_fatal(pool, world, messaging, scenario):
    await q(pool, "update clinic set messaging_mode='live' where id=%s returning id", [world["clinic"]])   # Live transport: Phase 7
    r = await broadcast.send_running_late(pool, messaging, clinic_id=world["clinic"], doctor_id=world["neha"], delay_min=30,
                                          created_by=None, now=fake_now(world))
    assert (r["targeted"], r["sent"], r["failed"]) == (3, 0, 3)                 # reported honestly, nothing crashed


async def test_quiet_hours_skip_the_broadcast_messages(pool, world, messaging, scenario):
    r = await broadcast.send_running_late(pool, messaging, clinic_id=world["clinic"], doctor_id=world["neha"], delay_min=30,
                                          created_by=None, now=ist_to_utc(world["day"], time(7, 0)))
    assert r["sent"] == 0 and r["skipped"] == {"quiet_hours": 4}   # at 07:00 the 08:30 visit is still ahead too


# ───────────────────────── HTTP ─────────────────────────
async def test_broadcast_endpoints(client, pool, world, scenario, monkeypatch):
    monkeypatch.setattr("app.routers.broadcast.utcnow", lambda: fake_now(world))
    h = token_for(world["recep_user"])
    pv = await client.get("/broadcasts/running-late/preview", headers=h, params={"doctor_id": str(world["neha"]), "delay_min": 30})
    assert pv.status_code == 200 and pv.json()["recipient_count"] == 3 and pv.json()["preview"] == BODY.format(t="10:30 AM")
    assert (await client.get("/broadcasts/running-late/preview", headers=h,
                             params={"doctor_id": str(world["neha"]), "delay_min": 20})).status_code == 422
    sent = await client.post("/broadcasts/running-late", headers=h, json={"doctor_id": str(world["neha"]), "delay_min": 30})
    assert sent.status_code == 200 and sent.json()["sent"] == 3
    dup = await client.post("/broadcasts/running-late", headers=h, json={"doctor_id": str(world["neha"]), "delay_min": 30})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "duplicate_broadcast"
    bad = await client.post("/broadcasts/running-late", headers=h, json={"doctor_id": str(world["neha"]), "delay_min": 25})
    assert bad.status_code == 422
    ghost = await client.post("/broadcasts/running-late", headers=h, json={"doctor_id": str(uuid.uuid4()), "delay_min": 15})
    assert ghost.status_code == 404


async def test_broadcast_requires_login(client, world):
    assert (await client.post("/broadcasts/running-late", json={"doctor_id": str(world["neha"]), "delay_min": 15})).status_code == 401
    assert (await client.get("/broadcasts/running-late/preview", params={"doctor_id": str(world["neha"]), "delay_min": 15})).status_code == 401
