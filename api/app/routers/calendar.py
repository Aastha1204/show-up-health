"""Private iCalendar feed per doctor (no login: the long random token in the URL is the credential)."""
from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..db import get_conn
from ..ratelimit import RateLimiter, client_ip
from ..services import ical
from ..timeutil import utcnow

router = APIRouter(tags=["calendar"])
feed_limiter = RateLimiter([(30, 60), (600, 3600)])      # calendar apps poll hourly; scripts guessing tokens do not
TOKEN = re.compile(r"^[0-9a-f]{64}$")


@router.get("/calendar/{token}.ics")
async def feed(token: str, request: Request, conn=Depends(get_conn)):
    retry = feed_limiter.check(client_ip(request))
    if retry is not None:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(retry)})
    doc = None
    if TOKEN.match(token):
        doc = await (await conn.execute(
            "select d.id, d.name, c.name as clinic_name from doctor d join clinic c on c.id=d.clinic_id where d.calendar_token=%s and d.active",
            [token])).fetchone()
    if not doc:
        raise HTTPException(404, "Not found")
    now = utcnow()
    rows = await (await conn.execute(
        """select a.id, a.start_at, a.end_at, a.status, a.updated_at, p.name as patient_name
           from appointment a join patient p on p.id=a.patient_id
           where a.doctor_id=%s and a.start_at > %s and a.start_at < %s order by a.start_at""",
        [doc["id"], now - timedelta(days=7), now + timedelta(days=120)])).fetchall()
    body = ical.build(doc["name"], doc["clinic_name"], rows, now)
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=300", "X-Robots-Tag": "noindex", "Content-Disposition": 'inline; filename="showup.ics"'})
