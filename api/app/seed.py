"""Deterministic demo seed for SmileCare Dental Clinic, Kalyan West (fictional).

`generate()` is pure (no DB) and asserts the headline numbers; `load()` writes it to Postgres.
Run:  python -m app.seed [--create-auth-users]
All seeded patients are is_demo_seed = true, so they can never receive real WhatsApp messages.
"""
from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from .timeutil import IST, UTC, ist_to_utc, ist_today, utcnow, weekday_key, window_of, parse_hhmm

SEED = 20260101
PATIENT_COUNT = 500
HISTORY_DAYS = 90
GO_LIVE_DAYS_AGO = 60
RECOVERED_SLOTS = 18
HOURS = [["10:00", "13:30"], ["17:00", "21:00"]]
WORKING_HOURS = {k: (HOURS if k != "sun" else []) for k in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}

TREATMENTS = [  # (name, minutes, recall_days, ticket ₹, weight)
    ("Consultation", 20, None, 500, 30),
    ("Scaling & polishing", 30, 180, 1500, 20),
    ("Filling", 45, None, 1200, 20),
    ("Root canal session", 60, None, 3500, 10),
    ("Braces adjustment", 20, 30, 800, 12),
    ("Extraction", 30, None, 1000, 8),
]
FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Sahil", "Karan", "Rahul", "Amit", "Sanjay", "Vikram",
         "Nikhil", "Pranav", "Omkar", "Shubham", "Rajesh", "Suresh", "Ganesh", "Sachin", "Mahesh", "Priya",
         "Anjali", "Neha", "Pooja", "Sneha", "Kavita", "Sunita", "Meera", "Riya", "Divya", "Shweta", "Swati",
         "Madhuri", "Aarti", "Rutuja", "Snehal", "Manasi", "Pallavi", "Shraddha", "Jyoti", "Anita", "Rekha",
         "Ketan", "Tushar", "Yash", "Harsh", "Ishaan", "Tanvi", "Nisha", "Komal"]
LAST = ["Sharma", "Patil", "Deshmukh", "Joshi", "Kulkarni", "Jadhav", "Pawar", "More", "Shinde", "Gaikwad",
        "Kale", "Chavan", "Bhosale", "Mehta", "Shah", "Gupta", "Verma", "Iyer", "Nair", "Reddy", "Singh",
        "Yadav", "Mishra", "Pandey", "Thakur", "Naik", "Sawant", "Kadam", "Salunkhe", "Wagh", "Desai", "Parab",
        "Gokhale", "Apte", "Bhat", "Rane", "Mhatre", "Tiwari", "Dubey", "Agarwal"]


class Det:
    """Deterministic uuid4 source."""
    def __init__(self, rng: random.Random):
        self.rng = rng

    def uuid(self) -> uuid.UUID:
        return uuid.UUID(int=self.rng.getrandbits(128), version=4)


def _pick_treatment(rng: random.Random, tts: list[dict]) -> dict:
    return rng.choices(tts, weights=[t["_w"] for t in tts])[0]


def _pack(rng: random.Random, d: date, tts: list[dict]) -> list[tuple[datetime, dict]]:
    """Lay appointments one after another (random 0/15-min gaps, always on the 15-minute grid) across the working ranges of IST date d."""
    ranges = [(parse_hhmm(a), parse_hhmm(b)) for a, b in WORKING_HOURS[weekday_key(d)]]
    if not ranges:
        return []
    placed, ri = [], 0
    cur = ist_to_utc(d, ranges[0][0])
    for tt in tts:
        dur = timedelta(minutes=tt["duration_min"])
        while True:
            if ri >= len(ranges):
                return placed
            rs, end = ist_to_utc(d, ranges[ri][0]), ist_to_utc(d, ranges[ri][1])
            start = cur + timedelta(minutes=rng.choice([0, 0, 0, 0, 0, 15]))
            # Snap UP to the 15-minute grid the product books on. (20-minute visits laid end to end would otherwise start at
            # 10:20, 10:40...: times that cannot be offered to the waitlist or shown as "current" when rescheduling.)
            start = rs + timedelta(minutes=-(-int((start - rs).total_seconds() // 60) // 15) * 15)
            if start + dur <= end:
                placed.append((start, tt))
                cur = start + dur
                break
            ri += 1
            if ri < len(ranges):
                cur = ist_to_utc(d, ranges[ri][0])
    return placed


def _names(rng: random.Random, n: int) -> list[str]:
    combos = [f"{f} {l}" for f in FIRST for l in LAST]
    rng.shuffle(combos)
    return combos[:n]


def generate(now: datetime | None = None, seed: int = SEED) -> dict[str, Any]:
    now = now or utcnow()
    rng = random.Random(seed)
    det = Det(rng)
    today = ist_today(now)
    ds: dict[str, Any] = {"now": now, "today": today}

    clinic_id = det.uuid()
    ds["clinic"] = dict(
        id=clinic_id, name="SmileCare Dental Clinic", address="Shop 4, Lotus Plaza, Station Road, Kalyan West, Maharashtra 421301",
        maps_link="https://maps.google.com/?q=Kalyan+West+Station+Road", parking_note="Free parking inside the society compound",
        phone="+912512345678", review_link="https://g.page/r/smilecare-demo/review", avg_ticket_value=1200,
        baseline_no_show_rate=0.14, go_live_date=today - timedelta(days=GO_LIVE_DAYS_AGO), messaging_mode="simulator",
    )
    docs = [("Dr. Neha Kulkarni", "#0d9488", "+919000000001"), ("Dr. Arjun Mehta", "#6366f1", "+919000000002")]
    ds["doctors"] = [dict(id=det.uuid(), clinic_id=clinic_id, name=n, colour=c, whatsapp_phone=p,
                          working_hours=WORKING_HOURS, active=True, is_demo_seed=True) for n, c, p in docs]
    ds["treatments"] = [dict(id=det.uuid(), clinic_id=clinic_id, name=n, duration_min=m, recall_after_days=r,
                             avg_ticket_value=v, _w=w) for n, m, r, v, w in TREATMENTS]
    tts = ds["treatments"]

    # ── patients (all demo seeds; fictional numbers, never messaged live) ──
    phones = rng.sample(range(10_000_000, 99_999_999), PATIENT_COUNT)
    ds["patients"] = [dict(
        id=det.uuid(), clinic_id=clinic_id, name=name, phone=f"+9190{ph}",
        language=rng.choices(["en", "hi", "mr"], weights=[50, 30, 20])[0],
        consent_source=rng.choice(["website_form", "reception", "reception", "import"]),
        consent_at=now - timedelta(days=rng.randint(30, 400)), opted_out=False, no_show_count=0, is_demo_seed=True,
    ) for name, ph in zip(_names(rng, PATIENT_COUNT), phones)]
    pats = ds["patients"]

    appts: list[dict] = []
    events: list[dict] = []

    def new_appt(start, tt, doctor, patient, status, source="dashboard", note=None) -> dict:
        a = dict(id=det.uuid(), clinic_id=clinic_id, patient_id=patient["id"], doctor_id=doctor["id"],
                 treatment_type_id=tt["id"], start_at=start, duration_min=tt["duration_min"], status=status,
                 source=source, avg_ticket_value=tt["avg_ticket_value"], recovered_via_waitlist=False,
                 internal_note=note, _day=start.astimezone(IST).date())
        appts.append(a)
        return a

    # ── 90 days of history ──
    for off in range(-HISTORY_DAYS, 0):
        d = today + timedelta(days=off)
        if not WORKING_HOURS[weekday_key(d)]:
            continue
        for _ in range(100):  # retry until the day lands in 20-24 appointments
            n = rng.randint(20, 24)
            plans = []
            for i, doc in enumerate(ds["doctors"]):
                k = n // 2 + (n % 2 if i == 0 else 0)
                plans.append((doc, _pack(rng, d, [_pick_treatment(rng, tts) for _ in range(k)])))
            if 20 <= sum(len(p) for _, p in plans) <= 24:
                break
        else:  # pragma: no cover
            raise AssertionError(f"could not pack {d}")
        used = rng.sample(pats, sum(len(p) for _, p in plans))
        it = iter(used)
        for doc, placed in plans:
            for start, tt in placed:
                new_appt(start, tt, doc, next(it), "arrived", "dashboard")

    # outcomes per 7-day block: exact counts so the weekly chart is stable and the headline numbers hold
    hist = [a for a in appts]
    for a in hist:
        if rng.random() < 0.06:
            a["status"] = "cancelled"
    gl = -GO_LIVE_DAYS_AGO  # day offset of go-live
    blocks = [(b, min(b + 7, gl)) for b in range(-HISTORY_DAYS, gl, 7)] + [(b, min(b + 7, 0)) for b in range(gl, 0, 7)]
    for b0, b1 in blocks:
        block = [a for a in hist if a["status"] == "arrived" and b0 <= (a["_day"] - today).days < b1]
        if b0 < gl:
            rate = 0.14  # before reminders
        else:  # falls from ~7.2% to ~5.2% as reminders bed in
            rate = 0.072 - 0.02 * (b0 - gl) / (GO_LIVE_DAYS_AGO - 1)
        rate *= 1 + rng.uniform(-0.08, 0.08)
        for a in rng.sample(block, round(rate * len(block))):
            a["status"] = "no_show"
    go_live = ds["clinic"]["go_live_date"]
    cand = [a for a in hist if a["status"] == "arrived" and a["_day"] >= go_live]
    for a in rng.sample(cand, RECOVERED_SLOTS):
        a["source"], a["recovered_via_waitlist"] = "waitlist", True

    # ── live days: today + next two calendar days, working days only ──
    live_days = [today + timedelta(days=i) for i in range(3) if WORKING_HOURS[weekday_key(today + timedelta(days=i))]]
    used_ids = set()
    live: list[dict] = []
    for di, d in enumerate(live_days):
        keep = 0.72 if di == 0 else 0.5
        for doc in ds["doctors"]:
            k = rng.randint(9, 11)
            for start, tt in _pack(rng, d, [_pick_treatment(rng, tts) for _ in range(k)]):
                if rng.random() > keep:
                    continue
                p = rng.choice([p for p in pats if p["id"] not in used_ids])
                used_ids.add(p["id"])
                live.append(new_appt(start, tt, doc, p, "pending", rng.choice(["dashboard", "website", "website"])))
    live.sort(key=lambda a: a["start_at"])
    past = [a for a in live if a["start_at"] + timedelta(minutes=a["duration_min"]) <= now]
    for a in past + [a for a in live if a["start_at"] <= now < a["start_at"] + timedelta(minutes=a["duration_min"])]:
        a["status"] = "arrived"
    if len(past) >= 3:
        past[1]["status"] = "no_show"
    future = [a for a in live if a["start_at"] > now]
    cancel_me = next(a for a in future if a["start_at"] >= now + timedelta(hours=3))
    cancel_me.update(status="confirmed", internal_note="DEMO: cancel me to trigger the waitlist offer")
    rest = [a for a in future if a is not cancel_me]
    if len(rest) > 6:
        rest[2]["status"], rest[4]["status"] = "rescheduled", "cancelled"
        rest[2]["internal_note"] = "Moved by patient via WhatsApp"
    pending_12h = 0
    for a in rest:
        if a["status"] in ("rescheduled", "cancelled"):
            continue
        soon = a["start_at"] < now + timedelta(hours=12)
        a["status"] = "pending" if rng.random() < (0.7 if soon else 0.45) else "confirmed"
        pending_12h += soon and a["status"] == "pending"
    for a in rest:  # guarantee a usable call list whenever the clinic has appointments in the next 12h
        if pending_12h >= 3:
            break
        if a["start_at"] < now + timedelta(hours=12) and a["status"] == "confirmed":
            a["status"], pending_12h = "pending", pending_12h + 1
    for a in live:
        events.append(dict(id=det.uuid(), clinic_id=clinic_id, appointment_id=a["id"], from_status=None,
                           to_status="pending", actor="reception", detail={"created": True},
                           created_at=now - timedelta(days=2)))
        if a["status"] != "pending":
            actor = "patient" if a["status"] in ("confirmed", "rescheduled", "cancelled") else "reception"
            events.append(dict(id=det.uuid(), clinic_id=clinic_id, appointment_id=a["id"], from_status="pending",
                               to_status=a["status"], actor=actor, detail={}, created_at=now - timedelta(hours=1)))

    # ── waitlist: entry #1 matches the "cancel me" slot ──
    free = [p for p in pats if p["id"] not in used_ids]
    cm_doc = next(x for x in ds["doctors"] if x["id"] == cancel_me["doctor_id"])
    cm_local = cancel_me["start_at"].astimezone(IST)
    consult = tts[0]
    wl = [
        (free[0], cm_doc["id"], cm_local.date(), window_of(cm_local.time()), 3),
        (free[1], None, live_days[0], "evening", 2),
        (free[2], ds["doctors"][1]["id"], live_days[-1], "morning", 1),
    ]
    ds["waitlist_entries"] = [dict(
        id=det.uuid(), clinic_id=clinic_id, patient_id=p["id"], doctor_id=doc, treatment_type_id=consult["id"],
        preferred_date=d, preferred_window=w, status="waiting", created_at=now - timedelta(hours=h),
    ) for p, doc, d, w, h in wl]

    # patient.no_show_count from history + today
    counts: dict[Any, int] = {}
    for a in appts:
        if a["status"] == "no_show":
            counts[a["patient_id"]] = counts.get(a["patient_id"], 0) + 1
    for p in pats:
        p["no_show_count"] = counts.get(p["id"], 0)

    # WhatsApp confirmation history for the post-go-live period: it drives the analytics "confirmation rate".
    # (A separate random stream, so nothing above changes.) Most visits that were kept had been confirmed by the
    # patient on WhatsApp; most no-shows had not. Recovered waitlist slots were booked by the patient themselves.
    erng = random.Random(seed + 1)
    edet = Det(erng)
    for a in hist:
        if a["_day"] < go_live or a["status"] not in ("arrived", "no_show"):
            continue
        base = dict(clinic_id=clinic_id, appointment_id=a["id"], detail={})
        if a["recovered_via_waitlist"]:
            events.append(dict(id=edet.uuid(), from_status=None, to_status="confirmed", actor="patient",
                               created_at=a["start_at"] - timedelta(hours=5), **base))
            continue
        events.append(dict(id=edet.uuid(), from_status=None, to_status="pending", actor="reception",
                           created_at=a["start_at"] - timedelta(hours=30), **base))
        if erng.random() < (0.8 if a["status"] == "arrived" else 0.2):
            events.append(dict(id=edet.uuid(), from_status="pending", to_status="confirmed", actor="patient",
                               created_at=a["start_at"] - timedelta(hours=18), **base))

    ds["appointments"], ds["appointment_events"] = appts, events
    ds["cancel_me_id"] = cancel_me["id"]
    ds["live_days"] = live_days
    check(ds)
    return ds


def no_show_rate(appts: list[dict]) -> float:
    done = [a for a in appts if a["status"] in ("arrived", "no_show")]
    return sum(a["status"] == "no_show" for a in done) / len(done)


def check(ds: dict[str, Any]) -> None:
    """The seed asserts its own headline numbers (spec section 9)."""
    today, go_live = ds["today"], ds["clinic"]["go_live_date"]
    hist = [a for a in ds["appointments"] if a["_day"] < today]
    pre = [a for a in hist if a["_day"] < go_live and (today - a["_day"]).days > GO_LIVE_DAYS_AGO]
    post = [a for a in hist if a["_day"] >= go_live]
    assert len(ds["patients"]) == PATIENT_COUNT and all(p["is_demo_seed"] for p in ds["patients"])
    assert 0.13 <= no_show_rate(pre) <= 0.15, f"pre-reminder no-show rate {no_show_rate(pre):.3f}"
    assert 0.05 <= no_show_rate(post) <= 0.07, f"post-go-live no-show rate {no_show_rate(post):.3f}"
    assert sum(a["recovered_via_waitlist"] for a in post) == RECOVERED_SLOTS
    per_day: dict[date, int] = {}
    for a in hist:
        per_day[a["_day"]] = per_day.get(a["_day"], 0) + 1
    assert per_day and all(20 <= n <= 24 for n in per_day.values()), "history days must have 20-24 appointments"
    live = [a for a in ds["appointments"] if a["_day"] >= today]
    soon = [a for a in live if a["status"] == "pending" and ds["now"] < a["start_at"] < ds["now"] + timedelta(hours=12)]
    within_12h = [a for a in live if ds["now"] < a["start_at"] < ds["now"] + timedelta(hours=12)
                  and a["status"] not in ("cancelled", "rescheduled") and a["id"] != ds["cancel_me_id"]]   # cancel-me is confirmed on purpose
    # On Sundays / outside clinic hours there may be nothing in the next 12h; the call list is then legitimately empty.
    assert len(soon) >= min(3, len(within_12h)), "need >=3 pending appointments in the next 12h for the call list"
    assert any(a["status"] == "cancelled" for a in live) and any(a["status"] == "rescheduled" for a in live)
    assert len(ds["waitlist_entries"]) == 3
    from .services.slots import is_slot_valid          # every seeded appointment must be one the product itself could book
    off = [a for a in ds["appointments"] if not is_slot_valid(WORKING_HOURS, a["start_at"], a["duration_min"])]
    assert not off, f"{len(off)} seeded appointments are off the 15-minute grid or outside working hours, e.g. {off[0]['start_at']}"


def _insert(cur, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    from psycopg.types.json import Jsonb
    cols = [c for c in rows[0] if not c.startswith("_")]
    sql = f"insert into {table} ({','.join(cols)}) values ({','.join(['%s'] * len(cols))})"
    cur.executemany(sql, [[Jsonb(r[c]) if isinstance(r[c], (dict, list)) else r[c] for c in cols] for r in rows])


def load(dsn: str, ds: dict[str, Any] | None = None) -> dict[str, Any]:
    """Wipe and reseed. Existing staff_profile rows survive, re-pointed at the new clinic."""
    import psycopg
    from psycopg.rows import dict_row
    ds = ds or generate()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("select user_id, email, role from staff_profile")
            staff = cur.fetchall()
            cur.execute("truncate clinic cascade")
            _insert(cur, "clinic", [ds["clinic"]])
            _insert(cur, "doctor", ds["doctors"])
            _insert(cur, "treatment_type", ds["treatments"])
            _insert(cur, "patient", ds["patients"])
            _insert(cur, "appointment", ds["appointments"])
            _insert(cur, "appointment_event", ds["appointment_events"])
            _insert(cur, "waitlist_entry", ds["waitlist_entries"])
            _insert(cur, "staff_profile", [dict(clinic_id=ds["clinic"]["id"], **s) for s in staff])
            cur.execute("select count(*) as n from appointment")
            assert cur.fetchone()["n"] == len(ds["appointments"])
    return ds


def create_auth_users(dsn: str, clinic_id) -> None:
    """Create the two demo Supabase Auth users (idempotent) and upsert their staff_profile rows."""
    import os
    import httpx
    import psycopg
    from .config import get_settings
    s = get_settings()
    if not (s.supabase_url and s.supabase_service_role_key):
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for --create-auth-users")
    hdr = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
    users = [("owner@smilecare.demo", os.getenv("DEMO_OWNER_PASSWORD", "ShowUp-Owner-2026"), "owner"),
             ("reception@smilecare.demo", os.getenv("DEMO_RECEPTION_PASSWORD", "ShowUp-Reception-2026"), "reception")]
    with httpx.Client(base_url=s.supabase_url.rstrip("/"), headers=hdr, timeout=30) as http, \
            psycopg.connect(dsn) as conn:
        for email, pw, role in users:
            r = http.post("/auth/v1/admin/users", json={"email": email, "password": pw, "email_confirm": True})
            if r.status_code in (200, 201):
                uid = r.json()["id"]
            else:  # already registered: find it
                lst = http.get("/auth/v1/admin/users", params={"per_page": 200}).json()["users"]
                uid = next(u["id"] for u in lst if u["email"] == email)
            conn.execute(
                """insert into staff_profile (clinic_id, user_id, email, role) values (%s,%s,%s,%s)
                   on conflict (user_id) do update set clinic_id=excluded.clinic_id, role=excluded.role""",
                [clinic_id, uid, email, role])
            print(f"staff ready: {email} ({role})")


if __name__ == "__main__":
    from .config import get_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-auth-users", action="store_true")
    args = ap.parse_args()
    dsn = get_settings().database_url
    data = load(dsn)
    print(f"seeded {len(data['patients'])} patients, {len(data['appointments'])} appointments; "
          f"cancel-me appointment {data['cancel_me_id']}")
    if args.create_auth_users:
        create_auth_users(dsn, data["clinic"]["id"])
