"""Owner analytics: every number checked against data built by hand (or against the seed's documented targets)."""
import asyncio
import uuid
from datetime import date, datetime, time, timedelta

import pytest

from app import seed
from app.services import appointments as svc
from app.timeutil import ist_to_utc, ist_today, utcnow
from .conftest import token_for
from .test_messaging_db import q

pytestmark = pytest.mark.db

SLOTS = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "17:00", "17:30", "18:00"]


def when(d: date, hhmm: str):
    h, m = hhmm.split(":")
    return ist_to_utc(d, time(int(h), int(m)))


async def make(pool, w, d: date, hhmm: str, *, status="arrived", doctor="neha", recovered=False, patient="p1"):
    async with pool.connection() as conn:
        return await svc.create_appointment(conn, clinic_id=w["clinic"], patient_id=w[patient], doctor_id=w[doctor],
                                            treatment_type_id=w["consult"], start_at=when(d, hhmm), enforce_hours=False,
                                            schedule_reminders=False, status=status, recovered_via_waitlist=recovered)


async def patient_confirmed(pool, w, appt):
    await q(pool, """insert into appointment_event (clinic_id, appointment_id, from_status, to_status, actor)
                     values (%s,%s,'pending','confirmed','patient') returning id""", [w["clinic"], appt["id"]])


def month_of(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


async def get(client, w, role="owner_user", **params):
    return await client.get("/analytics", headers=token_for(w[role]), params=params)


# ───────────────────────── access ─────────────────────────
async def test_only_the_owner_can_see_analytics(client, world):
    assert (await client.get("/analytics")).status_code == 401
    assert (await get(client, world, "recep_user")).status_code == 403
    assert (await get(client, world, "owner_user")).status_code == 200
    assert (await get(client, world, month="2026-13")).status_code == 422                 # not a month


# ───────────────────────── tiles ─────────────────────────
async def test_tiles_match_hand_computed_numbers(client, pool, world):
    d = world["day"]
    appts = []
    for i in range(6):                                             # 6 arrived (2 of them recovered from the waitlist)
        appts.append(await make(pool, world, d, SLOTS[i], status="arrived", recovered=i < 2))
    for i in range(6, 8):                                          # 2 no-shows
        appts.append(await make(pool, world, d, SLOTS[i], status="no_show"))
    for i in range(8, 10):                                         # 2 confirmed (settled, upcoming)
        appts.append(await make(pool, world, d, SLOTS[i], status="confirmed"))
    await make(pool, world, d, "10:00", status="pending", doctor="arjun")                     # not settled
    await make(pool, world, d, "10:30", status="cancelled", doctor="arjun", recovered=True)   # recovered but cancelled: not counted
    for a in appts[:3] + [appts[8]]:                               # 4 appointments the PATIENT confirmed on WhatsApp
        await patient_confirmed(pool, world, a)
    await q(pool, """insert into appointment_event (clinic_id, appointment_id, from_status, to_status, actor)
                     values (%s,%s,'pending','confirmed','reception') returning id""", [world["clinic"], appts[3]["id"]])  # phone: not counted

    j = (await get(client, world, month=month_of(d))).json()
    t = j["tiles"]
    assert (t["completed"], t["no_shows"], t["no_show_rate"]) == (8, 2, 0.25)               # 2 / (6 + 2)
    assert t["recovered_slots"] == 2 and t["recovered_since_go_live"] == 2
    assert t["revenue_saved"] == 2 * 1200                                                    # recovered x average ticket
    assert (t["settled"], t["confirmed_via_whatsapp"], t["confirmation_rate"]) == (10, 4, 0.4)
    assert j["clinic"] == {"baseline_no_show_rate": 0.14, "avg_ticket_value": 1200, "go_live_date": None, "plan_price": 2999}
    assert j["month"] == month_of(d)


async def test_empty_month_returns_nulls_not_errors(client, world):
    j = (await get(client, world, month="2019-03")).json()
    t = j["tiles"]
    assert t["no_show_rate"] is None and t["confirmation_rate"] is None
    assert (t["completed"], t["recovered_slots"], t["revenue_saved"]) == (0, 0, 0)
    assert j["simulated_share"] == 0.0 and len(j["weekly"]) == 13


async def test_month_filter_isolates_months(client, pool, world):
    this_month = ist_today().replace(day=1)
    last_month_day = this_month - timedelta(days=5)
    await make(pool, world, last_month_day, "10:00", status="no_show")
    await make(pool, world, this_month, "10:00", status="arrived")
    a = (await get(client, world, month=month_of(last_month_day))).json()["tiles"]
    b = (await get(client, world, month=month_of(this_month))).json()["tiles"]
    assert (a["completed"], a["no_shows"]) == (1, 1) and (b["completed"], b["no_shows"]) == (1, 0)


async def test_recovered_since_go_live_ignores_the_month_filter(client, pool, world):
    today = ist_today()
    await q(pool, "update clinic set go_live_date=%s returning id", [today - timedelta(days=40)])
    await make(pool, world, today - timedelta(days=35), "10:00", recovered=True)         # after go-live, last month
    await make(pool, world, today - timedelta(days=60), "10:00", recovered=True)         # BEFORE go-live: not counted
    j = (await get(client, world, month=month_of(today))).json()
    assert j["tiles"]["recovered_since_go_live"] == 1
    assert j["clinic"]["go_live_date"] == (today - timedelta(days=40)).isoformat()


# ───────────────────────── weekly series ─────────────────────────
async def test_weekly_series_is_13_ordered_zero_filled_weeks_with_correct_rates(client, pool, world):
    today = ist_today()
    monday = today - timedelta(days=today.weekday())
    w1, w3 = monday - timedelta(weeks=1), monday - timedelta(weeks=3)
    for i in range(4):
        await make(pool, world, w1 + timedelta(days=1), SLOTS[i], status="arrived")
    await make(pool, world, w1 + timedelta(days=2), "10:00", status="no_show")           # week -1: 5 completed, 1 no-show
    await make(pool, world, w1 + timedelta(days=2), "10:30", status="arrived", recovered=True)
    for i in range(4):
        await make(pool, world, w3 + timedelta(days=3), SLOTS[i], status="arrived")      # week -3: 4 completed, none missed
    weekly = (await get(client, world)).json()["weekly"]
    starts = [date.fromisoformat(x["week_start"]) for x in weekly]
    assert len(weekly) == 13 and starts == sorted(starts) and starts[-1] == monday
    assert all(s.weekday() == 0 for s in starts) and starts[0] == monday - timedelta(weeks=12)
    by = {date.fromisoformat(x["week_start"]): x for x in weekly}
    assert (by[w1]["completed"], by[w1]["no_shows"], by[w1]["no_show_rate"], by[w1]["recovered"]) == (6, 1, round(1 / 6, 4), 1)
    assert (by[w3]["completed"], by[w3]["no_show_rate"]) == (4, 0.0)
    assert by[monday - timedelta(weeks=2)] == {"week_start": (monday - timedelta(weeks=2)).isoformat(), "completed": 0,
                                               "no_shows": 0, "no_show_rate": None, "recovered": 0}   # empty week: null, not 0%


# ───────────────────────── simulated data + ROI ─────────────────────────
async def test_simulated_share_counts_seeded_patients(client, pool, world):
    today = ist_today()
    await q(pool, "update patient set is_demo_seed=true where id = any(%s) returning id", [[world["p1"], world["p2"]]])
    for i, p in enumerate(["p1", "p2", "p3", "p3"]):                # 2 of 4 appointments belong to seeded patients
        await make(pool, world, today - timedelta(days=3), SLOTS[i], patient=p)
    assert (await get(client, world)).json()["simulated_share"] == 0.5
    await q(pool, "update patient set is_demo_seed=false returning id")
    assert (await get(client, world)).json()["simulated_share"] == 0.0


async def test_roi_uses_recovered_slots_plus_no_shows_avoided_vs_baseline(client, pool, world):
    today = ist_today()
    for day in range(1, 5):                                          # 20 completed visits in the last 30 days, 1 no-show
        for i in range(5):
            await make(pool, world, today - timedelta(days=day), SLOTS[i], status="no_show" if (day, i) == (1, 0) else "arrived",
                       recovered=(day, i) in {(2, 0), (2, 1)})
    roi = (await get(client, world)).json()["roi"]
    assert roi["no_show_rate"] == 0.05 and roi["recovered"] == 2
    assert roi["avoided_no_shows"] == 1                              # int((0.14 - 0.05) * 20) = int(1.8)
    assert (roi["saved_appointments"], roi["value"], roi["plan_price"]) == (3, 3600, 2999)


async def test_roi_never_goes_negative_when_the_clinic_is_worse_than_baseline(client, pool, world):
    today = ist_today()
    for i in range(5):
        await make(pool, world, today - timedelta(days=2), SLOTS[i], status="no_show" if i < 3 else "arrived")   # 60% no-shows
    roi = (await get(client, world)).json()["roi"]
    assert roi["avoided_no_shows"] == 0 and roi["saved_appointments"] == 0 and roi["value"] == 0


# ───────────────────────── against the real seed ─────────────────────────
async def test_seed_numbers_show_up_in_analytics(client, dsn, world):
    ds = await asyncio.to_thread(seed.load, dsn)
    today = ds["today"]
    last_full_month = month_of((today.replace(day=1) - timedelta(days=1)))
    j = (await get(client, world, month=last_full_month)).json()
    weekly = j["weekly"]
    assert sum(x["recovered"] for x in weekly) == 18 and j["tiles"]["recovered_since_go_live"] == 18   # exactly 18 recovered slots
    rates = [x["no_show_rate"] for x in weekly if x["no_show_rate"] is not None]
    early, late = rates[:3], [x["no_show_rate"] for x in weekly[-4:-1] if x["no_show_rate"] is not None]
    assert 0.09 <= sum(early) / len(early) <= 0.18                    # ~14% before reminders
    assert 0.03 <= sum(late) / len(late) <= 0.09                      # 5-7% after
    assert j["simulated_share"] == 1.0                                # every seeded patient is flagged
    assert j["clinic"]["go_live_date"] == ds["clinic"]["go_live_date"].isoformat()
    conf = j["tiles"]["confirmation_rate"]
    assert j["tiles"]["settled"] > 300 and 0.6 <= conf <= 0.85
    assert j["tiles"]["revenue_saved"] == j["tiles"]["recovered_slots"] * 1200
