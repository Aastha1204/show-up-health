from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import jobs
from .config import get_settings
from .db import is_serverless, make_pool
from .messaging.service import MessagingService
from .routers import analytics, appointments, broadcast, calendar, demo, imports, inbox, internal, patients, public, settings, simulator, waitlist, webhooks

if sys.platform == "win32":  # psycopg async needs the selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

log = logging.getLogger("showup")
TICK_SECONDS = 30


_init_lock = asyncio.Lock()


async def ensure_state(app: FastAPI) -> None:
    """Create the DB pool and messaging service once. Called from lifespan AND (lazily) from a middleware, because some serverless
    hosts (Vercel) may not run ASGI lifespan events. Safe to call repeatedly and concurrently."""
    if hasattr(app.state, "pool"):
        return
    async with _init_lock:
        if hasattr(app.state, "pool"):
            return
        pool = make_pool(get_settings().database_url)
        await pool.open()
        app.state.pool = pool
        # Simulator "delivered/read" ticks are delayed background tasks; a serverless instance can freeze between requests,
        # so there the Simulator shows "Sent" only (everything else is unaffected).
        app.state.messaging = MessagingService(pool, sim_delays=None) if is_serverless() else MessagingService(pool)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await ensure_state(app)
    pool = app.state.pool
    scheduler = None
    # No in-process scheduler on serverless: drive POST /internal/tick from an external cron instead.
    if s.scheduler_enabled and not is_serverless():
        async def _tick():
            try:
                counts = await jobs.tick(pool, app.state.messaging)
                if counts:
                    log.info("tick: %s", dict(counts))
            except Exception:
                log.exception("tick failed")
        scheduler = AsyncIOScheduler()
        scheduler.add_job(_tick, "interval", seconds=TICK_SECONDS, max_instances=1, coalesce=True)
        scheduler.start()
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="ShowUp API · by Neovia AI", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=get_settings().cors_origins,
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def _lazy_state(request, call_next):
        if not hasattr(request.app.state, "pool"):
            await ensure_state(request.app)
        return await call_next(request)

    @app.get("/health")
    async def health():
        """Liveness: the process is up (what the host's health check polls)."""
        return {"ok": True}

    @app.get("/health/ready")
    async def ready():
        """Readiness: the database answers and the schema is migrated."""
        try:
            async with app.state.pool.connection() as conn:
                row = await (await conn.execute("select to_regclass('public.system_status') is not null as migrated")).fetchone()
        except Exception:
            from fastapi.responses import JSONResponse
            return JSONResponse({"ok": False, "database": False}, status_code=503)
        if not row["migrated"]:
            from fastapi.responses import JSONResponse
            return JSONResponse({"ok": False, "database": True, "migrated": False}, status_code=503)
        return {"ok": True, "database": True, "migrated": True}

    for r in (appointments, patients, waitlist, broadcast, inbox, analytics, imports, public, settings, webhooks, calendar, simulator, demo, internal):
        app.include_router(r.router)
    return app


app = create_app()
