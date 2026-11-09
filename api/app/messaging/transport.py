"""Transports. Only the transport differs between Live and Simulator; everything else is shared.

LiveTransport talks to the WhatsApp Cloud API (Graph API). It was written against Meta's documented request/response
shapes and is covered by tests that replay those shapes through a mock HTTP transport; it has NOT been exercised against
Meta's servers from this repository (that needs a real WhatsApp Business account)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

import httpx

from ..config import Settings, get_settings

log = logging.getLogger("showup.transport")


class TransportError(Exception):
    def __init__(self, message: str, code: str | None = None, transient: bool = False):
        super().__init__(message)
        self.code, self.transient = code, transient


@dataclass
class Delivery:
    """What a transport needs to deliver: already guarded, already persisted."""
    outbound_id: uuid.UUID
    to_phone: str
    kind: str
    body: str
    template_name: str | None
    language: str
    footer: str | None
    buttons: list | None
    list: dict | None
    is_demo_seed: bool
    params: list[str] | None = None        # ordered template variable values ({{1}}, {{2}}, ...)


class Transport(Protocol):
    name: str

    async def deliver(self, d: Delivery) -> str:
        """Return the provider message id (wamid)."""


class SimulatorTransport:
    name = "simulator"

    async def deliver(self, d: Delivery) -> str:
        return f"wamid.SIM{uuid.uuid4().hex[:28].upper()}"


# ───────────────────────── Live: WhatsApp Cloud API ─────────────────────────
# HTTP statuses and Meta error codes that mean "not accepted, safe to try again shortly".
RETRY_HTTP = {429, 500, 502, 503, 504}
TRANSIENT_CODES = {1, 2, 4, 17, 32, 613, 80007, 130429, 131056}
# Meta codes worth explaining in plain words in the message log (the code itself is always kept).
HINTS = {
    190: "the access token is invalid or has expired",
    100: "Meta rejected a parameter (check the template variables)",
    131030: "that number is not on the allowed recipient list (the Meta test number can only message 5 verified numbers)",
    131047: "the 24-hour customer-service window is closed: only an approved template can be sent",
    131026: "the number is not on WhatsApp, or has not accepted WhatsApp's terms",
    131048: "Meta limited sending because of spam reports",
    131051: "unsupported message type",
    132000: "template parameter count does not match",
    132001: "the template does not exist or is not approved in this language",
    132005: "the translated text is too long",
    132007: "the template violates WhatsApp's content policy",
    132012: "a template parameter has the wrong format",
    132015: "the template is paused for low quality",
    132016: "the template is disabled",
}


def graph_url(s: Settings, path: str) -> str:
    return f"https://graph.facebook.com/{s.wa_api_version}/{path}"


def build_payload(d: Delivery) -> dict:
    """The JSON body for POST /{phone-number-id}/messages (pure; unit-tested against Meta's documented shapes)."""
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": d.to_phone.lstrip("+")}
    if d.kind == "text":
        return {**base, "type": "text", "text": {"preview_url": False, "body": d.body}}
    if d.kind == "template":
        components = []
        if d.params:
            components.append({"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in d.params]})
        for i, b in enumerate(d.buttons or []):                # quick-reply payloads are set at send time (CONFIRM:{id}, ...)
            components.append({"type": "button", "sub_type": "quick_reply", "index": str(i),
                               "parameters": [{"type": "payload", "payload": b["payload"]}]})
        tpl = {"name": d.template_name, "language": {"code": d.language}}
        if components:
            tpl["components"] = components
        return {**base, "type": "template", "template": tpl}
    if d.kind == "interactive_list":
        lst = d.list or {}
        interactive = {"type": "list", "body": {"text": d.body},
                       "action": {"button": lst.get("button", "Choose"), "sections": [
                           {"title": s.get("title", ""), "rows": [
                               {k: v for k, v in {"id": r["id"], "title": r["title"], "description": r.get("description")}.items() if v}
                               for r in s["rows"]]} for s in lst.get("sections", [])]}}
        if lst.get("header"):
            interactive["header"] = {"type": "text", "text": lst["header"]}
        return {**base, "type": "interactive", "interactive": interactive}
    if d.kind == "interactive_buttons":
        return {**base, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": d.body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["text"]}} for b in (d.buttons or [])]}}}
    raise TransportError(f"unsupported message kind {d.kind!r}", "bad_kind")


def parse_meta_error(resp: httpx.Response) -> tuple[int | None, str]:
    """-> (meta error code, readable text). Keeps Meta's own words and adds a plain-language hint when we know one."""
    try:
        e = resp.json().get("error") or {}
    except ValueError:
        e = {}
    code = e.get("code")
    text = e.get("error_user_msg") or e.get("message") or f"HTTP {resp.status_code}"
    hint = HINTS.get(code) if isinstance(code, int) else None
    return (code if isinstance(code, int) else None), f"{text} ({hint})" if hint else text


class LiveTransport:
    name = "live"
    implemented = True

    def __init__(self, *, client: httpx.AsyncClient | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 settings: Callable[[], Settings] = get_settings, attempts: int = 3, backoff: tuple[float, ...] = (0.5, 1.5)):
        self._client, self._sleep, self._settings, self.attempts, self.backoff = client, sleep, settings, attempts, backoff

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        return self._client

    async def deliver(self, d: Delivery) -> str:
        if d.is_demo_seed:  # defence in depth: the service already routes seeds to the simulator
            raise TransportError("refusing to send live WhatsApp to a demo-seed recipient", "demo_seed")
        s = self._settings()
        if not (s.wa_access_token and s.wa_phone_number_id):
            raise TransportError("WhatsApp is not configured (WA_ACCESS_TOKEN / WA_PHONE_NUMBER_ID)", "live_not_configured")
        payload = build_payload(d)
        url = graph_url(s, f"{s.wa_phone_number_id}/messages")
        headers = {"Authorization": f"Bearer {s.wa_access_token}"}
        for attempt in range(self.attempts):
            last = attempt + 1 >= self.attempts
            try:
                resp = await self._http().post(url, json=payload, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                # Nothing reached Meta, so trying again cannot send twice.
                if last:
                    raise TransportError(f"could not reach WhatsApp: {type(e).__name__}", "network", transient=True)
                await self._sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
                continue
            except httpx.HTTPError as e:
                # e.g. a read timeout: the request may well have been delivered. Retrying could message the patient twice
                # (Meta has no idempotency key), so it is NOT retried and NOT marked transient.
                raise TransportError(f"no answer from WhatsApp ({type(e).__name__}); it may or may not have been sent", "timeout_ambiguous")
            if resp.status_code == 200:
                try:
                    return resp.json()["messages"][0]["id"]
                except (ValueError, KeyError, IndexError, TypeError):
                    raise TransportError("WhatsApp accepted the request but returned no message id", "bad_response")
            code, text = parse_meta_error(resp)
            transient = resp.status_code in RETRY_HTTP or (code in TRANSIENT_CODES)
            if transient and not last:
                try:
                    wait = min(float(resp.headers.get("retry-after", "")), 5.0)
                except ValueError:
                    wait = self.backoff[min(attempt, len(self.backoff) - 1)]
                log.warning("WhatsApp send retry %s/%s after %s %s", attempt + 1, self.attempts, resp.status_code, code)
                await self._sleep(wait)
                continue
            raise TransportError(text, f"meta_{code}" if code else f"http_{resp.status_code}", transient=transient)
        raise TransportError("WhatsApp send failed", "unknown", transient=True)   # unreachable; keeps type-checkers calm
