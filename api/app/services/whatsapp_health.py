"""WhatsApp health check for the Settings screen: is Live ready, and what is missing?

Reads Meta's Graph API with the configured token. It never returns a secret: only booleans, Meta's own public facts
(display number, verified name, template approval status) and plain-language problems."""
from __future__ import annotations

import time
from datetime import datetime

import httpx

from ..config import Settings
from ..messaging import templates
from ..messaging.transport import graph_url, parse_meta_error

CACHE_SECONDS = 30
_cache: dict[str, tuple[float, dict]] = {}
OUR_TEMPLATES = sorted(templates.load_all())


def config_flags(s: Settings) -> dict[str, bool]:
    return {"access_token": bool(s.wa_access_token), "phone_number_id": bool(s.wa_phone_number_id),
            "business_account_id": bool(s.wa_business_account_id), "app_secret": bool(s.wa_app_secret),
            "verify_token": bool(s.wa_verify_token)}


async def _get(client: httpx.AsyncClient, s: Settings, path: str, params: dict) -> httpx.Response:
    return await client.get(graph_url(s, path), params=params, headers={"Authorization": f"Bearer {s.wa_access_token}"}, timeout=10)


async def check(s: Settings, *, mode: str, last_webhook: dict | None, client: httpx.AsyncClient | None = None,
                refresh: bool = False, now: datetime | None = None) -> dict:
    flags = config_flags(s)
    key = f"{s.wa_phone_number_id}|{s.wa_business_account_id}|{bool(s.wa_access_token)}"
    hit = _cache.get(key)
    if hit and not refresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        graph = hit[1]
    else:
        graph = await _graph(s, flags, client)
        _cache[key] = (time.monotonic(), graph)

    blockers: list[str] = []
    warnings: list[str] = []
    if not flags["access_token"] or not flags["phone_number_id"]:
        blockers.append("Set WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID on the API server.")
    elif graph["token_valid"] is False:
        blockers.append(graph["token_error"] or "Meta rejected the access token.")
    elif graph["phone"] is None and graph["token_valid"] is None:
        warnings.append(graph["token_error"] or "Could not reach Meta to check the token.")
    if not flags["app_secret"]:
        blockers.append("Set WA_APP_SECRET: without it webhook signatures cannot be verified and every inbound event is rejected.")
    if not flags["verify_token"]:
        blockers.append("Set WA_VERIFY_TOKEN: Meta needs it to subscribe to the webhook.")
    if not flags["business_account_id"]:
        warnings.append("WA_BUSINESS_ACCOUNT_ID is not set, so template approval status cannot be checked or submitted from here.")
    elif graph["templates"] is not None:
        bad = [t["name"] for t in graph["templates"] if t["status"] != "APPROVED"]
        if bad:
            warnings.append("Not approved yet: " + ", ".join(n.replace("showup_", "") for n in bad) + ". Buttons and templates must match the approved versions.")
    if last_webhook is None:
        warnings.append("Meta has not called the webhook yet. Set the callback URL and verify token in the Meta app, and subscribe to “messages”.")

    last_at = last_webhook["updated_at"] if last_webhook else None
    age = int(((now or datetime.now(last_at.tzinfo)) - last_at).total_seconds()) if last_at else None
    return {
        "mode": mode, "config": flags,
        "token": {"checked": graph["token_checked"], "valid": graph["token_valid"], "error": graph["token_error"]},
        "phone": graph["phone"], "templates": graph["templates"],
        "webhook": {"last_received_at": last_at, "age_seconds": age, "last": last_webhook["value"] if last_webhook else None},
        "blockers": blockers, "warnings": warnings, "ready": not blockers,
    }


async def _graph(s: Settings, flags: dict, client: httpx.AsyncClient | None) -> dict:
    out = {"token_checked": False, "token_valid": None, "token_error": None, "phone": None, "templates": None}
    if not (flags["access_token"] and flags["phone_number_id"]):
        return out
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        out["token_checked"] = True
        try:
            r = await _get(client, s, s.wa_phone_number_id, {"fields": "display_phone_number,verified_name,quality_rating,code_verification_status"})
        except httpx.HTTPError as e:
            out["token_error"] = f"Could not reach Meta ({type(e).__name__})."
            return out
        if r.status_code == 200:
            j = r.json()
            out["token_valid"] = True
            out["phone"] = {k: j.get(k) for k in ("display_phone_number", "verified_name", "quality_rating", "code_verification_status")}
        else:
            code, text = parse_meta_error(r)
            if code == 190 or r.status_code == 401:
                out["token_valid"], out["token_error"] = False, f"Meta rejected the access token: {text}"
            elif code == 100:
                out["token_valid"], out["token_error"] = False, f"WA_PHONE_NUMBER_ID looks wrong, or the token cannot see it: {text}"
            else:
                out["token_error"] = f"Meta returned an error: {text}"
            return out
        if flags["business_account_id"]:
            try:
                r = await _get(client, s, f"{s.wa_business_account_id}/message_templates", {"fields": "name,status,language", "limit": 100})
                if r.status_code == 200:
                    by: dict[str, list[dict]] = {}
                    for t in r.json().get("data", []):
                        by.setdefault(t.get("name", ""), []).append(t)
                    out["templates"] = [{"name": n, **_best(by.get(n, []))} for n in OUR_TEMPLATES]
            except httpx.HTTPError:
                pass
        return out
    finally:
        if own:
            await client.aclose()


_RANK = ["APPROVED", "PENDING", "IN_APPEAL", "PAUSED", "REJECTED", "DISABLED"]


def _best(entries: list[dict]) -> dict:
    """One status per template name: the best across its languages; NOT_SUBMITTED if Meta has never seen it."""
    if not entries:
        return {"status": "NOT_SUBMITTED", "languages": []}
    entries = sorted(entries, key=lambda e: _RANK.index(e.get("status")) if e.get("status") in _RANK else len(_RANK))
    return {"status": entries[0].get("status", "UNKNOWN"), "languages": sorted({e.get("language", "") for e in entries})}


def go_live_blockers_sync(s: Settings) -> list[str]:
    """The cheap, offline part of "can we go Live?" (no network)."""
    f = config_flags(s)
    out = []
    if not (f["access_token"] and f["phone_number_id"]):
        out.append("Set WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID.")
    if not f["app_secret"]:
        out.append("Set WA_APP_SECRET.")
    if not f["verify_token"]:
        out.append("Set WA_VERIFY_TOKEN.")
    return out


async def submit_templates(s: Settings, language: str, client: httpx.AsyncClient | None = None) -> list[dict]:
    """POST each template definition to Meta for approval (the same JSON you could paste into WhatsApp Manager)."""
    own = client is None
    client = client or httpx.AsyncClient()
    results = []
    try:
        for name in OUR_TEMPLATES:
            if language not in templates.get_template(name)["languages"]:
                results.append({"name": name, "result": "skipped", "detail": f"no {language} version of this template yet"})
                continue
            try:
                r = await client.post(graph_url(s, f"{s.wa_business_account_id}/message_templates"), json=templates.to_meta_template(name, language),
                                      headers={"Authorization": f"Bearer {s.wa_access_token}"}, timeout=15)
            except httpx.HTTPError as e:
                results.append({"name": name, "result": "error", "detail": f"could not reach Meta ({type(e).__name__})"})
                continue
            if r.status_code == 200:
                results.append({"name": name, "result": "submitted", "detail": (r.json().get("status") or "PENDING")})
                continue
            try:
                e = r.json().get("error", {})
            except ValueError:
                e = {}
            if e.get("error_subcode") == 2388023:      # "template with this name and language already exists"
                results.append({"name": name, "result": "already_exists", "detail": "Meta already has this template"})
            else:
                code, text = parse_meta_error(r)
                results.append({"name": name, "result": "error", "detail": f"{code or r.status_code}: {text}"})
        return results
    finally:
        if own:
            await client.aclose()
