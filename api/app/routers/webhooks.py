from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

from psycopg.types.json import Jsonb

from ..db import get_conn

from ..config import get_settings
from ..messaging import webhook
from ..services import payments
from ..timeutil import utcnow

router = APIRouter(tags=["webhooks"])


@router.get("/webhooks/whatsapp")
async def verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
                 challenge: str = Query("", alias="hub.challenge")):
    """Meta's subscription handshake: echo hub.challenge iff the verify token matches."""
    s = get_settings()
    if mode == "subscribe" and s.wa_verify_token and token == s.wa_verify_token:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(403, "verification failed")


@router.post("/webhooks/whatsapp")
async def receive(request: Request, background: BackgroundTasks, conn=Depends(get_conn)):
    """Verify the signature over the RAW body, answer 200 immediately, process in the background."""
    raw = await request.body()
    if not webhook.verify_signature(raw, request.headers.get("x-hub-signature-256"), get_settings().wa_app_secret):
        raise HTTPException(403, "bad signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "invalid json")
    events = webhook.parse_webhook(payload)
    await conn.execute(                      # for the Settings health check: "last webhook received"
        """insert into system_status (key, value) values ('last_webhook', %s)
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        [Jsonb({"events": len(events), "statuses": sum(isinstance(e, webhook.StatusEvent) for e in events)})])
    background.add_task(webhook.handle_payload, request.app.state.pool, payload, request.app.state.messaging)
    return {"ok": True}


@router.post("/webhooks/razorpay")
async def razorpay(request: Request, conn=Depends(get_conn)):
    """Signed by Razorpay (X-Razorpay-Signature over the raw body). Idempotent: Razorpay retries, and we answer 200 for any
    well-signed event, including ones we do not care about."""
    raw = await request.body()
    if not payments.verify_signature(raw, request.headers.get("x-razorpay-signature"), get_settings().razorpay_webhook_secret):
        raise HTTPException(403, "bad signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "invalid json")
    return {"ok": True, "result": await payments.handle_webhook(conn, payload, utcnow())}
