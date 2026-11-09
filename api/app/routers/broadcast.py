from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..auth import Staff, current_staff
from ..db import get_conn
from ..services import broadcast as svc
from ..services.appointments import DomainError
from ..timeutil import utcnow

router = APIRouter(prefix="/broadcasts", tags=["broadcast"])


def _raise(e: DomainError):
    raise HTTPException(e.status_code, {"code": e.code, "message": e.message})


@router.get("/running-late/preview")
async def preview(doctor_id: UUID, delay_min: int = Query(...), staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    """What the modal shows before sending: the exact message and who will get it."""
    try:
        return await svc.preview(conn, staff.clinic_id, doctor_id, delay_min, utcnow())
    except DomainError as e:
        _raise(e)


class RunningLate(BaseModel):
    doctor_id: UUID
    delay_min: int


@router.post("/running-late")
async def send(body: RunningLate, request: Request, staff: Staff = Depends(current_staff)):
    try:
        return await svc.send_running_late(request.app.state.pool, request.app.state.messaging, clinic_id=staff.clinic_id,
                                           doctor_id=body.doctor_id, delay_min=body.delay_min, created_by=staff.user_id,
                                           now=utcnow())
    except DomainError as e:
        _raise(e)
