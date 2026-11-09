"""Operations: the migration runner, the webhook-receipt record behind the health check, and readiness."""
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import conninfo

from app import migrate as mig
from app.messaging import meta_payloads as mp, webhook
from .conftest import MIGRATIONS, token_for

pytestmark = pytest.mark.db


@pytest.fixture
def scratch_db():
    """A throwaway empty database on the same server as the test database, dropped afterwards."""
    base = conninfo.conninfo_to_dict(os.environ["TEST_DATABASE_URL"])
    name = f"showup_mig_{uuid.uuid4().hex[:10]}"
    admin = conninfo.make_conninfo(**{**base, "dbname": "postgres"})
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'create database "{name}"')
    dsn = conninfo.make_conninfo(**{**base, "dbname": name})
    yield dsn
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'drop database if exists "{name}" with (force)')


def tables(dsn):
    with psycopg.connect(dsn) as c:
        return {r[0] for r in c.execute("select table_name from information_schema.tables where table_schema='public'").fetchall()}


def files():
    return [f.name for f in sorted(MIGRATIONS.glob("*.sql"))]


@pytest.fixture
def copy_of_migrations(tmp_path):
    d = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, d)
    return d


# ───────────────────────── migration runner ─────────────────────────
def test_a_fresh_database_gets_every_migration_in_order(scratch_db):
    assert mig.migrate(scratch_db) == files()
    t = tables(scratch_db)
    assert {"clinic", "appointment", "system_status", "schema_migrations", "waitlist_offer"} <= t
    with psycopg.connect(scratch_db) as c:
        assert [r[0] for r in c.execute("select filename from schema_migrations order by filename").fetchall()] == files()
        assert c.execute("select count(*) from pg_constraint where conname='no_overlap_per_doctor'").fetchone()[0] == 1   # the double-booking guard


def test_running_again_does_nothing(scratch_db):
    mig.migrate(scratch_db)
    assert mig.migrate(scratch_db) == []


def test_only_new_files_are_applied(scratch_db, copy_of_migrations):
    mig.migrate(scratch_db, copy_of_migrations)
    (copy_of_migrations / "006_extra.sql").write_text("create table extra_thing (id int);")
    assert mig.migrate(scratch_db, copy_of_migrations) == ["006_extra.sql"]
    assert "extra_thing" in tables(scratch_db)


def test_a_failing_migration_changes_nothing_and_is_not_recorded(scratch_db, copy_of_migrations):
    mig.migrate(scratch_db, copy_of_migrations)
    bad = copy_of_migrations / "006_bad.sql"
    bad.write_text("create table half_done (id int); select 1/0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        mig.migrate(scratch_db, copy_of_migrations)
    assert "half_done" not in tables(scratch_db)                                           # rolled back as a unit
    with psycopg.connect(scratch_db) as c:
        assert c.execute("select count(*) from schema_migrations where filename='006_bad.sql'").fetchone()[0] == 0
    bad.write_text("create table half_done (id int);")                                     # fix it and carry on
    assert mig.migrate(scratch_db, copy_of_migrations) == ["006_bad.sql"]


def test_a_hand_built_database_is_refused_until_baselined(scratch_db):
    with psycopg.connect(scratch_db, autocommit=True) as c:                                # set up by pasting the SQL, as the README used to say
        for f in sorted(MIGRATIONS.glob("*.sql")):
            c.execute(f.read_text(encoding="utf-8"))
    with pytest.raises(mig.MigrationError, match="--baseline"):
        mig.migrate(scratch_db)
    assert mig.migrate(scratch_db, baseline=True) == files()                               # records, runs nothing
    assert mig.migrate(scratch_db) == []
    with pytest.raises(mig.MigrationError, match="only for a database with no migration history"):
        mig.migrate(scratch_db, baseline=True)


def test_empty_directory_is_an_error(scratch_db, tmp_path):
    with pytest.raises(mig.MigrationError, match="no migration files"):
        mig.migrate(scratch_db, tmp_path)


def test_two_containers_starting_together_apply_each_migration_once(scratch_db):
    results, errors = [], []

    def run():
        try:
            results.append(mig.migrate(scratch_db))
        except Exception as e:                                                             # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=run) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    applied = [f for r in results for f in r]
    assert sorted(applied) == files() and len(applied) == len(set(applied))                # no file ran twice


# ───────────────────────── webhook receipt + readiness ─────────────────────────
def signed(payload):
    raw = json.dumps(payload).encode()
    return raw, {"X-Hub-Signature-256": webhook.sign(raw, "test-app-secret"), "Content-Type": "application/json"}


async def test_a_valid_webhook_is_recorded_for_the_health_check_and_a_forged_one_is_not(client, pool, world):
    async with pool.connection() as c:
        assert (await (await c.execute("select count(*) as n from system_status where key='last_webhook'")).fetchone())["n"] == 0
    raw, h = signed(mp.status_payload("wamid.X", "delivered", "+919876500000"))
    assert (await client.post("/webhooks/whatsapp", content=raw, headers={**h, "X-Hub-Signature-256": "sha256=forged"})).status_code == 403
    async with pool.connection() as c:
        assert (await (await c.execute("select count(*) as n from system_status")).fetchone())["n"] == 0
    assert (await client.post("/webhooks/whatsapp", content=raw, headers=h)).status_code == 200
    async with pool.connection() as c:
        row = await (await c.execute("select value, updated_at from system_status where key='last_webhook'")).fetchone()
    assert row["value"] == {"events": 1, "statuses": 1}
    raw2, h2 = signed(mp.text_payload("+919876500000", "hello", msg_id="rcpt-2"))
    await client.post("/webhooks/whatsapp", content=raw2, headers=h2)
    async with pool.connection() as c:
        again = await (await c.execute("select value, updated_at from system_status where key='last_webhook'")).fetchone()
    assert again["value"] == {"events": 1, "statuses": 0} and again["updated_at"] >= row["updated_at"]     # one row, updated in place


async def test_readiness_checks_the_database_and_the_schema(client, pool, world):
    assert (await client.get("/health")).json() == {"ok": True}
    r = await client.get("/health/ready")
    assert r.status_code == 200 and r.json() == {"ok": True, "database": True, "migrated": True}
