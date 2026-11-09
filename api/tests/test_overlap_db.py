"""Double-booking is a database guarantee. These tests need Postgres with btree_gist (marker: db)."""
import asyncio

import psycopg.errors
import pytest

from app.services import appointments as svc
from .conftest import at

pytestmark = pytest.mark.db


async def book(pool, w, doctor, patient, tt, hhmm, **kw):
    async with pool.connection() as conn:
        return await svc.create_appointment(
            conn, clinic_id=w["clinic"], patient_id=w[patient], doctor_id=w[doctor],
            treatment_type_id=w[tt], start_at=at(w["day"], hhmm), **kw)


async def test_long_then_short_overlap_is_rejected(pool, world):
    await book(pool, world, "neha", "p1", "rct", "10:00")          # 10:00-11:00
    with pytest.raises(svc.Conflict):
        await book(pool, world, "neha", "p2", "consult", "10:30")  # 10:30-10:50 — a unique index would miss this


async def test_back_to_back_is_allowed(pool, world):
    await book(pool, world, "neha", "p1", "rct", "10:00")
    await book(pool, world, "neha", "p2", "consult", "11:00")


async def test_same_time_different_doctors_is_allowed(pool, world):
    await book(pool, world, "neha", "p1", "consult", "10:00")
    await book(pool, world, "arjun", "p2", "consult", "10:00")


async def test_cancelled_appointment_frees_the_slot(pool, world):
    a = await book(pool, world, "neha", "p1", "consult", "10:00")
    async with pool.connection() as conn:
        await svc.set_status(conn, clinic_id=world["clinic"], appt_id=a["id"], to_status="cancelled")
    await book(pool, world, "neha", "p2", "consult", "10:00")


async def test_database_itself_rejects_overlap_even_if_the_app_is_bypassed(pool, world):
    a = await book(pool, world, "neha", "p1", "rct", "10:00")
    async with pool.connection() as conn:
        with pytest.raises(psycopg.errors.ExclusionViolation):
            await conn.execute(
                """insert into appointment (clinic_id, patient_id, doctor_id, start_at, duration_min)
                   values (%s,%s,%s,%s,20)""", [world["clinic"], world["p2"], world["neha"], at(world["day"], "10:30")])


async def test_end_at_is_maintained_by_trigger(pool, world):
    a = await book(pool, world, "neha", "p1", "rct", "10:00")
    assert (a["end_at"] - a["start_at"]).total_seconds() == 3600


async def test_two_concurrent_overlapping_bookings_exactly_one_wins(pool, world):
    results = await asyncio.gather(
        book(pool, world, "neha", "p1", "rct", "10:00"),
        book(pool, world, "neha", "p2", "consult", "10:30"),
        return_exceptions=True,
    )
    wins = [r for r in results if isinstance(r, dict)]
    losses = [r for r in results if isinstance(r, svc.Conflict)]
    assert len(wins) == 1 and len(losses) == 1, results


async def test_many_concurrent_bookings_for_one_slot_exactly_one_wins(pool, world):
    pats = ["p1", "p2", "p3"] * 3
    results = await asyncio.gather(*[book(pool, world, "neha", p, "consult", "10:00") for p in pats],
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, svc.Conflict) for r in results) == len(pats) - 1


async def test_moving_into_an_occupied_slot_conflicts_and_leaves_original_untouched(pool, world):
    await book(pool, world, "neha", "p1", "rct", "10:00")
    b = await book(pool, world, "neha", "p2", "consult", "12:00")
    async with pool.connection() as conn:
        with pytest.raises(svc.Conflict):
            await svc.move_appointment(conn, clinic_id=world["clinic"], appt_id=b["id"],
                                       start_at=at(world["day"], "10:30"))
    async with pool.connection() as conn:
        cur = await svc.get_appointment(conn, world["clinic"], b["id"])
    assert cur["start_at"] == at(world["day"], "12:00") and cur["status"] == "pending"


async def test_move_to_a_free_slot_marks_rescheduled_and_logs_an_event(pool, world):
    a = await book(pool, world, "neha", "p1", "consult", "10:00")
    async with pool.connection() as conn:
        moved = await svc.move_appointment(conn, clinic_id=world["clinic"], appt_id=a["id"],
                                           start_at=at(world["day"], "17:30"))
        cur = await conn.execute("select to_status, actor from appointment_event where appointment_id=%s order by created_at",
                                 [a["id"]])
        events = await cur.fetchall()
    assert moved["status"] == "rescheduled"
    assert [e["to_status"] for e in events] == ["pending", "rescheduled"]


async def test_appointments_stored_utc_and_listed_by_ist_day(pool, world):
    # 00:15 IST is the previous day in UTC; it must still be listed under the IST date.
    async with pool.connection() as conn:
        await svc.create_appointment(conn, clinic_id=world["clinic"], patient_id=world["p1"], doctor_id=world["neha"],
                                     treatment_type_id=world["consult"], start_at=at(world["day"], "00:15"),
                                     enforce_hours=False)
        cur = await conn.execute("select start_at at time zone 'UTC' as u from appointment")
        row = await cur.fetchone()
    assert row["u"].date() == world["day"].fromordinal(world["day"].toordinal() - 1)
