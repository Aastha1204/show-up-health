"""Test harness.

Pure tests (slots, timezone, phone) run anywhere. Tests marked `db` need a real Postgres with btree_gist:

    docker compose up -d db
    set TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54329/showup_test   (create that DB first)

The DB is dropped and rebuilt from /db/migrations once per test session, so point it at a throwaway database.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

JWT_SECRET = "test-secret-test-secret-test-secret-123456"
os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
os.environ["DEMO_MODE"] = "true"
os.environ["WA_APP_SECRET"] = "test-app-secret"
os.environ["WA_VERIFY_TOKEN"] = "verify-me"
os.environ["INTERNAL_TICK_SECRET"] = "tick-secret"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SUPABASE_URL"] = ""   # dev-login only exists when no Supabase project is configured
os.environ["PUBLIC_BOOKING_ALLOWED_ORIGINS"] = "http://localhost:3100,https://smilecare-clinic.example"
# The app's OWN database must be the test database: /demo/reset re-seeds (wipes) whatever DATABASE_URL points at,
# and a test must never be able to touch the development data.
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import jwt  # noqa: E402
import psycopg  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from app.db import make_pool  # noqa: E402
from app.main import create_app  # noqa: E402
from app.timeutil import ist_to_utc, ist_today  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
HOURS = [["10:00", "13:30"], ["17:00", "21:00"]]
WORKING_HOURS = {k: (HOURS if k != "sun" else []) for k in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs a real Postgres (TEST_DATABASE_URL)")


def pytest_collection_modifyitems(config, items):
    if os.getenv("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set (needs Postgres with btree_gist)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def future_monday(weeks_ahead: int = 2) -> date:
    d = ist_today() + timedelta(days=7 * weeks_ahead)
    return d - timedelta(days=d.weekday())


def at(d: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return ist_to_utc(d, time(int(h), int(m)))


@pytest.fixture(scope="session")
def dsn():
    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("drop schema public cascade")
        c.execute("create schema public")
        for f in sorted(MIGRATIONS.glob("*.sql")):
            c.execute(f.read_text(encoding="utf-8"))
    return url


@pytest.fixture
def world(dsn):
    """A fresh clinic with two doctors, treatments, patients and two staff users."""
    ids = {k: uuid.uuid4() for k in ["clinic", "neha", "arjun", "consult", "rct", "p1", "p2", "p3",
                                      "owner_user", "recep_user", "stranger"]}
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as c:
        c.execute("truncate clinic cascade")
        c.execute("truncate system_status")   # not tied to a clinic, so the cascade does not reach it
        c.execute("insert into clinic (id, name) values (%s, 'Test Clinic')", [ids["clinic"]])
        for key, name, ph in [("neha", "Dr. Neha", "+919000000001"), ("arjun", "Dr. Arjun", "+919000000002")]:
            c.execute("insert into doctor (id, clinic_id, name, whatsapp_phone, working_hours) values (%s,%s,%s,%s,%s)",
                      [ids[key], ids["clinic"], name, ph, Jsonb(WORKING_HOURS)])
        c.execute("insert into treatment_type (id, clinic_id, name, duration_min, avg_ticket_value) values (%s,%s,'Consultation',20,500)",
                  [ids["consult"], ids["clinic"]])
        c.execute("insert into treatment_type (id, clinic_id, name, duration_min, avg_ticket_value) values (%s,%s,'Root canal session',60,3500)",
                  [ids["rct"], ids["clinic"]])
        for i, k in enumerate(["p1", "p2", "p3"]):
            c.execute("insert into patient (id, clinic_id, name, phone, consent_source) values (%s,%s,%s,%s,'reception')",
                      [ids[k], ids["clinic"], f"Patient {i + 1}", f"+91987650000{i}"])
        c.execute("insert into staff_profile (clinic_id, user_id, email, role) values (%s,%s,'o@x','owner')",
                  [ids["clinic"], ids["owner_user"]])
        c.execute("insert into staff_profile (clinic_id, user_id, email, role) values (%s,%s,'r@x','reception')",
                  [ids["clinic"], ids["recep_user"]])
    ids["day"] = future_monday()
    return ids


@pytest.fixture
async def pool(dsn):
    p = make_pool(dsn)
    await p.open()
    yield p
    await p.close()


@pytest.fixture
def messaging(pool):
    from app.messaging.service import MessagingService
    return MessagingService(pool, sim_delays=None)   # tests drive Simulator statuses explicitly


@pytest.fixture
async def client(pool, messaging):
    app = create_app()
    app.state.pool = pool
    app.state.messaging = messaging
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.app = app          # tests reach app.state (e.g. to inject a fake Graph API client)
        yield c


def token_for(user_id) -> dict:
    t = jwt.encode({"sub": str(user_id), "aud": "authenticated"}, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
async def clinic_open_24h(pool, world):
    """Proactive sends obey quiet hours and the real clock may fall inside them; tests that send offers/broadcasts
    neutralise quiet hours (00:00-00:00). Quiet-hours behaviour has its own tests with an explicit clock."""
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as c:
        c.execute("update clinic set quiet_hours_start='00:00', quiet_hours_end='00:00' where id=%s", [world["clinic"]])


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    from app.ratelimit import booking_limiter, slots_limiter
    booking_limiter.reset()
    slots_limiter.reset()
    yield
