"""Builders for payloads in Meta's documented WhatsApp Cloud API webhook shape.
Used by the Simulator transport (status events), tests, and mirrored by the web Simulator UI."""
from __future__ import annotations

import time
import uuid

PHONE_NUMBER_ID = "SIMULATOR"


def _wrap(value: dict) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA_ID", "changes": [
        {"field": "messages", "value": {"messaging_product": "whatsapp",
                                        "metadata": {"display_phone_number": "15550000000",
                                                     "phone_number_id": PHONE_NUMBER_ID}, **value}}]}]}


def wa_id(phone_e164: str) -> str:
    """Meta sends numbers without the leading '+'."""
    return phone_e164.lstrip("+")


def status_payload(wamid: str, status: str, recipient_phone: str, errors: list[dict] | None = None,
                   ts: int | None = None) -> dict:
    st = {"id": wamid, "status": status, "timestamp": str(ts or int(time.time())),
          "recipient_id": wa_id(recipient_phone)}
    if errors:
        st["errors"] = errors
    return _wrap({"statuses": [st]})


def _message(phone: str, mtype: str, body: dict, context_wamid: str | None, msg_id: str | None, ts: int | None) -> dict:
    msg = {"from": wa_id(phone), "id": msg_id or f"wamid.IN{uuid.uuid4().hex[:24].upper()}",
           "timestamp": str(ts or int(time.time())), "type": mtype, **body}
    if context_wamid:
        msg["context"] = {"from": "15550000000", "id": context_wamid}
    return _wrap({"contacts": [{"profile": {"name": "Patient"}, "wa_id": wa_id(phone)}], "messages": [msg]})


def button_payload(phone: str, payload: str, text: str, context_wamid: str | None = None,
                   msg_id: str | None = None, ts: int | None = None) -> dict:
    """Template quick-reply tap: type 'button'."""
    return _message(phone, "button", {"button": {"payload": payload, "text": text}}, context_wamid, msg_id, ts)


def list_reply_payload(phone: str, row_id: str, title: str, description: str = "",
                       context_wamid: str | None = None, msg_id: str | None = None, ts: int | None = None) -> dict:
    return _message(phone, "interactive", {"interactive": {"type": "list_reply", "list_reply": {
        "id": row_id, "title": title, "description": description}}}, context_wamid, msg_id, ts)


def button_reply_payload(phone: str, button_id: str, title: str, context_wamid: str | None = None,
                         msg_id: str | None = None, ts: int | None = None) -> dict:
    """Interactive reply-button tap (inside the 24h window)."""
    return _message(phone, "interactive", {"interactive": {"type": "button_reply", "button_reply": {
        "id": button_id, "title": title}}}, context_wamid, msg_id, ts)


def text_payload(phone: str, body: str, msg_id: str | None = None, ts: int | None = None) -> dict:
    return _message(phone, "text", {"text": {"body": body}}, None, msg_id, ts)
