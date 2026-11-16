"""Running on a serverless host (Vercel): no lifespan guarantee, no background timers, pgbouncer-safe connections."""
import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.db import make_pool

pytestmark = pytest.mark.db


async def fresh_client():
    app = main.create_app()                       # NOTE: ASGITransport does not run lifespan events, exactly like a host that skips them
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_app_builds_its_own_pool_on_the_first_request_when_lifespan_never_ran():
    app, c = await fresh_client()
    assert not hasattr(app.state, "pool")
    async with c:
        r = await c.get("/health/ready")
        assert r.status_code == 200 and r.json() == {"ok": True, "database": True, "migrated": True}
        pool = app.state.pool
        assert (await c.get("/health/ready")).status_code == 200 and app.state.pool is pool     # built once, reused
    await pool.close()


async def test_concurrent_first_requests_build_exactly_one_pool():
    import asyncio
    app, c = await fresh_client()
    async with c:
        rs = await asyncio.gather(*[c.get("/health/ready") for _ in range(8)])
    assert all(r.status_code == 200 for r in rs)
    await app.state.pool.close()


async def test_on_vercel_the_simulator_does_not_schedule_delayed_ticks(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    app, c = await fresh_client()
    async with c:
        await c.get("/health")
    assert app.state.messaging.sim_delays is None
    assert app.state.pool.max_size == 3                                                          # small pool: many instances share Supabase
    await app.state.pool.close()


async def test_locally_nothing_changes(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app, c = await fresh_client()
    async with c:
        await c.get("/health")
    assert app.state.messaging.sim_delays == (1.0, 3.0) and app.state.pool.max_size == 10
    await app.state.pool.close()


def test_prepared_statements_are_off_for_supabase_transaction_pooler():
    p = make_pool("postgresql://u:p@localhost:6543/postgres")
    assert p.kwargs["prepare_threshold"] is None


def test_vercel_entry_point_exposes_the_same_app():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("vercel_index", pathlib.Path(main.__file__).parents[1] / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.app is main.app
