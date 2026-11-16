from __future__ import annotations

import os
from typing import AsyncIterator

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def is_serverless() -> bool:
    """True on Vercel (it sets VERCEL=1). There, no timer or background task may be relied on between requests."""
    return bool(os.environ.get("VERCEL"))


def make_pool(dsn: str, *, max_size: int | None = None, **kw) -> AsyncConnectionPool:
    """`prepare_threshold=None` turns off server-side prepared statements: Supabase's transaction pooler (port 6543, pgbouncer)
    does not support them, and the cost of not using them is negligible here. Serverless keeps the pool small (many instances)."""
    size = max_size or (3 if is_serverless() else 10)
    return AsyncConnectionPool(dsn, kwargs={"row_factory": dict_row, "prepare_threshold": None}, open=False,
                               min_size=1, max_size=size, **kw)


async def get_conn(request: Request) -> AsyncIterator:
    """One connection per request; commits on success, rolls back on exception."""
    async with request.app.state.pool.connection() as conn:
        yield conn
