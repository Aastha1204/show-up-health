from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import Staff, current_staff
from ..db import get_conn
from ..services import importer as imp
from ..timeutil import utcnow

router = APIRouter(prefix="/import", tags=["import"])


def _fail(e: imp.ImportError_):
    raise HTTPException(e.status, {"code": e.code, "message": e.message})


class PreviewBody(BaseModel):
    csv_text: str | None = None          # an uploaded file's text...
    sheet_url: str | None = None         # ...or a published / link-shared Google Sheet
    mapping: dict[str, str | None] | None = None


@router.post("/preview")
async def preview(body: PreviewBody, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    """Parse, guess/apply the column mapping and validate every row WITHOUT writing anything.
    The returned `csv_text` is what the commit step must send back (so the commit sees exactly what was previewed)."""
    try:
        if bool(body.csv_text) == bool(body.sheet_url):
            raise imp.ImportError_("Upload a CSV file or paste a Google Sheet link (one of the two).", "source_required")
        text = body.csv_text if body.csv_text else await imp.fetch_sheet_csv(body.sheet_url)
        headers, rows = imp.parse_csv(text)
        mapping = {f: (body.mapping or {}).get(f) if body.mapping else None for f in imp.FIELDS}
        if not body.mapping:
            mapping = imp.guess_mapping(headers)
        mapping = {f: (c if c in headers else None) for f, c in mapping.items()}   # ignore columns that do not exist
        out = {"csv_text": text, "headers": headers, "mapping": mapping,
               "fields": [{"key": f, "label": imp.LABELS[f], "required": f in imp.REQUIRED} for f in imp.FIELDS],
               "total_rows": len(rows), "needs_mapping": any(not mapping.get(f) for f in imp.REQUIRED),
               "rows": [], "counts": {"total": len(rows), "ok": 0, "errors": 0, "warnings": 0}}
        if not out["needs_mapping"]:
            results = await imp.validate_rows(conn, staff.clinic_id, rows, mapping, utcnow())
            out["rows"] = [r.public() for r in results]
            out["counts"] = imp.summarise(results)
        return out
    except imp.ImportError_ as e:
        _fail(e)


class CommitBody(BaseModel):
    csv_text: str
    mapping: dict[str, str | None]
    consent_confirmed: bool = False
    send_confirmations: bool = False


@router.post("/commit")
async def commit(body: CommitBody, staff: Staff = Depends(current_staff), conn=Depends(get_conn)):
    if body.consent_confirmed is not True:
        raise HTTPException(422, {"code": "consent_required", "message":
                                  "Please confirm these patients agreed to receive WhatsApp appointment updates."})
    try:
        _, rows = imp.parse_csv(body.csv_text)
        results = await imp.validate_rows(conn, staff.clinic_id, rows, body.mapping, utcnow())      # never trust the preview
        done = await imp.commit(conn, staff.clinic_id, results, send_confirmations=body.send_confirmations, now=utcnow())
        return {**done, "counts": imp.summarise(results)}
    except imp.ImportError_ as e:
        _fail(e)
