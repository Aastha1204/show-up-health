from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Request

from .. import jobs
from ..config import get_settings

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/tick")
async def tick(request: Request, x_internal_secret: str | None = Header(None)):
    """Backup driver for the scheduler (an external cron can call this). Protected by a shared header secret."""
    secret = get_settings().internal_tick_secret
    if not secret or not x_internal_secret or not hmac.compare_digest(secret, x_internal_secret):
        raise HTTPException(403, "forbidden")
    counts = await jobs.tick(request.app.state.pool, request.app.state.messaging)
    return dict(counts)
