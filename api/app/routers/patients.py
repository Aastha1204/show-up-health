from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import schemas
from ..auth import Staff, current_staff
from ..db import get_conn
from ..services import appointments as svc

router = APIRouter(prefix="/patients", tags=["patients"])

COLS = "id,name,phone,language,consent_source,consent_at,opted_out,opted_out_at,no_show_count,is_demo_seed"


@router.get("")
async def search_patients(q: str = Query("", max_length=60), limit: int = Query(20, le=100), offset: int = Query(0, ge=0),
                          staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    like = f"%{q.strip().lower()}%"
    cur = await conn.execute(
        f"select {COLS} from patient where clinic_id=%s and (lower(name) like %s or phone like %s) "
        "order by lower(name), id limit %s offset %s", [staff.clinic_id, like, f"%{q.strip()}%", limit, offset])
    return await cur.fetchall()


@router.post("", status_code=201)
async def create_patient(body: schemas.PatientIn, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    p = await svc.get_or_create_patient(conn, staff.clinic_id, body.model_dump())
    return {k: p[k] for k in COLS.split(",")}
