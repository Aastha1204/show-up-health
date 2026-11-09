from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import Staff, require_role
from ..db import get_conn
from ..services import analytics
from ..timeutil import utcnow

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("")
async def get_analytics(month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                        staff: Staff = Depends(require_role("owner")), conn=Depends(get_conn)):
    """Owner only. `month` = YYYY-MM (default: the current IST month); the weekly series is always the last 13 weeks."""
    clinic = await (await conn.execute("select * from clinic where id=%s", [staff.clinic_id])).fetchone()
    return await analytics.compute(conn, clinic, month, utcnow())
