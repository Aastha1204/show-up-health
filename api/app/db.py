from __future__ import annotations

from typing import AsyncIterator

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def make_pool(dsn: str, **kw) -> AsyncConnectionPool:
    return AsyncConnectionPool(dsn, kwargs={"row_factory": dict_row}, open=False, min_size=1, max_size=10, **kw)


async def get_conn(request: Request) -> AsyncIterator:
    """One connection per request; commits on success, rolls back on exception."""
    async with request.app.state.pool.connection() as conn:
        yield conn
