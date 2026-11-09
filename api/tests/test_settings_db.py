"""Owner Settings: validation, the medical-content guard on patient-visible text, effects on scheduling, template preview,
the WhatsApp health check and the Live switch (against a fake Graph API)."""
import json
import uuid
from datetime import time, timedelta

import httpx
import pytest

from app import jobs
from app.config import get_settings
from app.messaging import meta_payloads as mp, webhook
from app.services import whatsapp_health as wh
from app.timeutil import ist_to_utc, to_ist
from .conftest import at, token_for
from .test_messaging_db import book, frozen, job_rows, q

pytestmark = pytest.mark.db
SECRET = "SECRET-TOKEN-XYZ"


def owner(w): return token_for(w["owner_user"])


@pytest.fixture(autouse=True)
def _fresh_health_cache():
    wh._cache.clear()
    yield
    wh._cache.clear()


def configure(monkeypatch, **extra):
    s = get_settings()
    vals = {"wa_access_token": SECRET, "wa_phone_number_id": "1055550001", "wa_business_account_id": "2077770002", "public_api_url": "https://api.example", **extra}
    for k, v in vals.items():
        monkeypatch.setattr(s, k, v)


class Graph:
    """A fake Graph API: records requests; answers per path."""
    def __init__(self, *, phone=None, templates=None, token_ok=True, submit=None):
        self.requests, self.phone, self.templates, self.token_ok, self.submit = [], phone or {"display_phone_number": "+1 555 000 0001", "verified_name": "SmileCare", "quality_rating": "GREEN"}, templates or [], token_ok, submit or (lambda name: httpx.Response(200, json={"id": "1", "status": "PENDING"}))

    async def __call__(self, req: httpx.Request):
        self.requests.append(req)
        path = req.url.path
        if req.method == "GET" and path.endswith("/1055550001"):
            return httpx.Response(200, json=self.phone) if self.token_ok else httpx.Response(401, json={"error": {"code": 190, "message": "Error validating access token: Session has expired"}})
        if req.method == "GET" and path.endswith("/message_templates"):
            return httpx.Response(200, json={"data": self.templates})
        if req.method == "POST" and path.endswith("/message_templates"):
            return self.submit(json.loads(req.content)["name"])
        return httpx.Response(404, json={"error": {"code": 100, "message": "nope"}})

    def install(self, client):
        client.app.state.graph_client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return self


# ───────────────────────── access + read ─────────────────────────
async def test_only_the_owner_may_use_settings(client, world):
    for method, url in [("get", "/settings"), ("patch", "/settings/clinic"), ("get", "/settings/health"), ("get", "/settings/templates"),
                        ("post", "/settings/doctors"), ("post", "/settings/treatments"), ("put", "/settings/messaging-mode")]:
        assert (await getattr(client, method)(url)).status_code == 401, url
        assert (await getattr(client, method)(url, headers=token_for(world["recep_user"]))).status_code == 403, url
    assert (await client.get("/settings", headers=owner(world))).status_code == 200


async def test_settings_snapshot(client, pool, world):
    await book(pool, world, "10:30")
    j = (await client.get("/settings", headers=owner(world))).json()
    assert j["clinic"]["name"] == "Test Clinic" and j["clinic"]["quiet_hours_start"] == "21:00" and j["clinic"]["quiet_hours_end"] == "08:00"
    assert j["clinic"]["baseline_no_show_rate"] == 0.14 and j["clinic"]["reminder_settings"]["day_before"]["offset_min"] == 1440
    neha = next(d for d in j["doctors"] if d["name"] == "Dr. Neha")
    assert neha["upcoming_appointments"] == 1 and neha["working_hours"]["mon"] == [["10:00", "13:30"], ["17:00", "21:00"]]
    assert {t["name"] for t in j["treatment_types"]} == {"Consultation", "Root canal session"}


# ───────────────────────── clinic details ─────────────────────────
async def test_clinic_details_save_and_validate(client, world):
    h = owner(world)
    r = await client.patch("/settings/clinic", headers=h, json={
        "name": "  Kalyan   Smiles  ", "address": "Shop 1, Station Road", "phone": "98765 43210", "avg_ticket_value": 1500,
        "baseline_no_show_rate": 0.2, "waitlist_offer_ttl_min": 30, "quiet_hours_start": "22:00", "quiet_hours_end": "07:30",
        "review_link": "https://g.page/r/x/review", "maps_link": "", "go_live_date": "2026-10-01"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["name"], c["phone"], c["avg_ticket_value"], c["waitlist_offer_ttl_min"], c["quiet_hours_start"], c["maps_link"], c["go_live_date"]) == \
           ("Kalyan Smiles", "+919876543210", 1500, 30, "22:00", None, "2026-10-01")
    assert (await client.get("/settings", headers=h)).json()["clinic"]["name"] == "Kalyan Smiles"


@pytest.mark.parametrize("bad", [{"maps_link": "javascript:alert(1)"}, {"review_link": "not a link"}, {"phone": "12345"}, {"waitlist_offer_ttl_min": 0},
                                 {"waitlist_offer_ttl_min": 500}, {"baseline_no_show_rate": 2}, {"avg_ticket_value": -1}, {"quiet_hours_start": "25:00"},
                                 {"quiet_hours_end": "8am"}, {"name": ""}, {"name": "   "}, {}])
async def test_clinic_rejects_bad_values(client, world, bad):
    r = await client.patch("/settings/clinic", headers=owner(world), json=bad)
    assert r.status_code == 422
    assert (await client.get("/settings", headers=owner(world))).json()["clinic"]["name"] == "Test Clinic"      # nothing half-saved


@pytest.mark.parametrize("field,value", [("name", "Root Canal Specialists"), ("address", "Next to the extraction centre"), ("parking_note", "Free parking, no pain")])
async def test_text_used_in_patient_messages_cannot_contain_medical_terms(client, world, field, value):
    r = await client.patch("/settings/clinic", headers=owner(world), json={field: value})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "medical_content" and "never mention treatments" in r.json()["detail"]["message"]


async def test_clinic_name_may_not_contain_a_treatment_name(client, world):
    r = await client.patch("/settings/clinic", headers=owner(world), json={"name": "Best Consultation Clinic"})
    assert r.status_code == 422 and "consultation" in r.json()["detail"]["message"].lower()


# ───────────────────────── reminders + quiet hours (and their effect) ─────────────────────────
async def test_reminder_settings_merge_and_validate(client, world):
    h = owner(world)
    r = await client.patch("/settings/clinic", headers=h, json={"reminder_settings": {"day_before": {"enabled": False}, "two_hour": {"offset_min": 180}}})
    rs = r.json()["reminder_settings"]
    assert rs["day_before"] == {"enabled": False, "offset_min": 1440} and rs["two_hour"]["offset_min"] == 180      # merged, not replaced
    assert rs["confirmation"]["enabled"] is True and rs["daily_summary"]["at"] == "08:00"                       # untouched
    for bad in ({"fax": {"enabled": True}}, {"day_before": {"colour": "red"}}, {"day_before": {"offset_min": 5}}, {"two_hour": {"offset_min": "soon"}},
                {"post_visit": {"enabled": "yes"}}, {"daily_summary": {"at": "8am"}}, {"day_before": "on"}):
        assert (await client.patch("/settings/clinic", headers=h, json={"reminder_settings": bad})).status_code == 422, bad


async def test_changed_reminder_settings_change_what_gets_scheduled(client, pool, world):
    h = owner(world)
    await client.patch("/settings/clinic", headers=h, json={"reminder_settings": {"day_before": {"enabled": False}, "two_hour": {"offset_min": 180}}})
    a = await book(pool, world, "13:00", now=frozen(world) - timedelta(days=2))
    rows = {j["type"]: j for j in await job_rows(pool, a["id"])}
    assert "day_before" not in rows                                                      # switched off
    assert to_ist(rows["two_hour"]["due_at"]).strftime("%H:%M") == "10:00"                # 13:00 minus 3 h (it was 11:00 with the default 2 h)


async def test_quiet_hours_setting_is_what_the_pipeline_uses(client, pool, world):
    await client.patch("/settings/clinic", headers=owner(world), json={"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"})
    a = await book(pool, world, "10:30", now=ist_to_utc(world["day"] - timedelta(days=1), time(20, 30)))
    conf = next(j for j in await job_rows(pool, a["id"]) if j["type"] == "confirmation")
    assert to_ist(conf["due_at"]).strftime("%a %H:%M") == "Mon 09:00"                    # 20:30 is now quiet: it waits until the new 09:00


# ───────────────────────── doctors ─────────────────────────
HOURS = {"mon": [["09:00", "12:00"], ["16:00", "19:00"]], "tue": [["09:00", "12:00"]]}


async def test_add_and_edit_a_doctor(client, world):
    h = owner(world)
    r = await client.post("/settings/doctors", headers=h, json={"name": "  Dr.  Isha   Rao ", "whatsapp_phone": "98765 11111", "colour": "#ff8800", "working_hours": HOURS})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["name"] == "Dr. Isha Rao" and d["whatsapp_phone"] == "+919876511111" and d["active"] is True and d["is_demo_seed"] is False
    assert set(d["working_hours"]) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"} and d["working_hours"]["wed"] == [] and d["working_hours"]["mon"][1] == ["16:00", "19:00"]
    p = await client.patch(f"/settings/doctors/{d['id']}", headers=h, json={"colour": "#112233", "working_hours": {"mon": [["10:00", "11:00"]]}})
    assert p.json()["colour"] == "#112233" and p.json()["working_hours"]["mon"] == [["10:00", "11:00"]] and p.json()["working_hours"]["tue"] == []


@pytest.mark.parametrize("hours", [{"mon": [["12:00", "09:00"]]}, {"mon": [["09:00", "12:00"], ["11:00", "13:00"]]}, {"mon": [["09:10", "12:00"]]},
                                   {"funday": [["09:00", "12:00"]]}, {"mon": [["9:00", "12:00"]]}, {"mon": "all day"}, {"mon": [["09:00"]]}])
async def test_bad_working_hours_are_refused(client, world, hours):
    r = await client.post("/settings/doctors", headers=owner(world), json={"name": "Dr. Test", "working_hours": hours})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_hours"


async def test_doctor_rules_duplicate_name_medical_name_last_doctor_and_unknown(client, world):
    h = owner(world)
    assert (await client.post("/settings/doctors", headers=h, json={"name": "dr. NEHA"})).status_code == 409           # names stay unambiguous
    assert (await client.post("/settings/doctors", headers=h, json={"name": "Dr. Root Canal"})).status_code == 422       # appears in messages
    assert (await client.post("/settings/doctors", headers=h, json={})).status_code == 422
    assert (await client.patch(f"/settings/doctors/{world['arjun']}", headers=h, json={"active": False})).status_code == 200
    last = await client.patch(f"/settings/doctors/{world['neha']}", headers=h, json={"active": False})
    assert last.status_code == 409 and last.json()["detail"]["code"] == "last_doctor"
    assert (await client.patch(f"/settings/doctors/{uuid.uuid4()}", headers=h, json={"active": True})).status_code == 404


async def test_changing_hours_never_touches_bookings_but_says_how_many_fall_outside(client, pool, world):
    await book(pool, world, "10:30")
    await book(pool, world, "17:30", patient="p2")
    r = await client.patch(f"/settings/doctors/{world['neha']}", headers=owner(world), json={"working_hours": {d: [["10:00", "13:30"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat")}})
    assert r.json()["appointments_outside_hours"] == 1                                    # the 17:30 evening visit
    assert (await q(pool, "select count(*) as n from appointment where status='pending'"))[0]["n"] == 2   # both still booked


# ───────────────────────── treatments ─────────────────────────
async def test_add_and_edit_treatments(client, world):
    h = owner(world)
    r = await client.post("/settings/treatments", headers=h, json={"name": " Teeth  Whitening ", "duration_min": 45, "recall_after_days": 365, "avg_ticket_value": 4000})
    assert r.status_code == 201 and r.json()["name"] == "Teeth Whitening"
    tid = r.json()["id"]
    p = await client.patch(f"/settings/treatments/{tid}", headers=h, json={"duration_min": 60, "recall_after_days": None})
    assert p.json()["duration_min"] == 60 and p.json()["recall_after_days"] is None
    assert (await client.patch(f"/settings/treatments/{uuid.uuid4()}", headers=h, json={"name": "X"})).status_code == 404


@pytest.mark.parametrize("bad", [{"name": "Consultation", "duration_min": 20}, {"name": "x", "duration_min": 3}, {"name": "x", "duration_min": 22},
                                 {"name": "x", "duration_min": 500}, {"name": "x"}, {"duration_min": 20}, {"name": "x", "duration_min": 20, "recall_after_days": 0}])
async def test_bad_treatments_are_refused(client, world, bad):
    assert (await client.post("/settings/treatments", headers=owner(world), json=bad)).status_code in (409, 422)


async def test_a_treatment_name_that_would_block_every_message_is_refused(client, world):
    await client.patch("/settings/clinic", headers=owner(world), json={"name": "SmileCare Dental Clinic"})
    r = await client.post("/settings/treatments", headers=owner(world), json={"name": "Dental", "duration_min": 30})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "collides_with_messages"


# ───────────────────────── templates ─────────────────────────
async def test_template_preview_and_language_fallback(client, world):
    en = (await client.get("/settings/templates", headers=owner(world))).json()
    assert len(en) == 9 and all("{{" not in t["body"] and t["language"] == "en" and not t["fallback"] for t in en)
    db = next(t for t in en if t["name"] == "showup_day_before")
    assert db["buttons"] == ["Confirm", "Reschedule", "Cancel"] and db["category"] == "UTILITY"
    assert next(t for t in en if t["name"] == "showup_daily_summary")["recipient"] == "doctor"
    assert next(t for t in en if t["name"] == "showup_post_visit")["category_note"]                    # the marketing-category warning
    hi = (await client.get("/settings/templates?language=hi", headers=owner(world))).json()
    for t in hi:                                                                                       # patient templates are translated; the doctor's summary stays English
        assert (t["fallback"], t["language"]) == ((True, "en") if t["name"] == "showup_daily_summary" else (False, "hi")), t["name"]
    assert (await client.get("/settings/templates?language=fr", headers=owner(world))).status_code == 422


# ───────────────────────── health + Live switch + submission ─────────────────────────
async def test_health_without_credentials_lists_what_is_missing_and_leaks_nothing(client, world):
    j = (await client.get("/settings/health", headers=owner(world))).json()
    assert j["ready"] is False and j["mode"] == "simulator"
    assert j["config"]["access_token"] is False and j["token"]["checked"] is False
    assert any("WA_ACCESS_TOKEN" in b for b in j["blockers"])
    assert any("not called the webhook" in w for w in j["warnings"]) and j["webhook"]["last_received_at"] is None
    assert j["webhook_path"] == "/webhooks/whatsapp" and j["api_version"] == "v21.0"


async def test_health_with_a_valid_token_shows_the_number_templates_and_never_the_secret(client, world, monkeypatch):
    configure(monkeypatch)
    g = Graph(templates=[{"name": "showup_day_before", "status": "APPROVED", "language": "en"}, {"name": "showup_day_before", "status": "REJECTED", "language": "hi"},
                         {"name": "showup_recall", "status": "PENDING", "language": "en"}, {"name": "someone_elses", "status": "APPROVED", "language": "en"}]).install(client)
    r = await client.get("/settings/health", headers=owner(world))
    j = r.json()
    assert SECRET not in r.text and "TOKEN" not in json.dumps(j["config"])                              # booleans only
    assert j["ready"] is True and j["blockers"] == [] and j["token"] == {"checked": True, "valid": True, "error": None}
    assert j["phone"]["verified_name"] == "SmileCare" and j["webhook_url"] == "https://api.example/webhooks/whatsapp"
    st = {t["name"]: t["status"] for t in j["templates"]}
    assert st["showup_day_before"] == "APPROVED" and st["showup_recall"] == "PENDING" and st["showup_post_visit"] == "NOT_SUBMITTED"
    assert "someone_elses" not in st and len(st) == 9                                                   # only our own templates
    assert any("Not approved yet" in w for w in j["warnings"])
    assert g.requests[0].headers["authorization"] == f"Bearer {SECRET}"


async def test_health_reports_an_expired_token_as_a_blocker_and_caches_the_check(client, world, monkeypatch):
    configure(monkeypatch)
    g = Graph(token_ok=False).install(client)
    j = (await client.get("/settings/health", headers=owner(world))).json()
    assert j["token"]["valid"] is False and j["ready"] is False and any("access token" in b for b in j["blockers"])
    n = len(g.requests)
    await client.get("/settings/health", headers=owner(world))
    assert len(g.requests) == n                                                                         # cached: the page can poll freely
    await client.get("/settings/health?refresh=true", headers=owner(world))
    assert len(g.requests) > n                                                                          # "Re-check" really re-checks


async def test_health_shows_when_meta_last_called_the_webhook(client, pool, world):
    raw = json.dumps(mp.status_payload("wamid.Z", "delivered", "+919876500000")).encode()
    await client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": webhook.sign(raw, "test-app-secret")})
    j = (await client.get("/settings/health", headers=owner(world))).json()
    assert j["webhook"]["last_received_at"] and j["webhook"]["age_seconds"] < 60 and j["webhook"]["last"]["events"] == 1
    assert not any("not called the webhook" in w for w in j["warnings"])


async def test_live_is_blocked_until_ready_and_simulator_is_always_allowed(client, pool, world, monkeypatch):
    h = owner(world)
    blocked = await client.put("/settings/messaging-mode", headers=h, json={"mode": "live"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "live_blocked" and blocked.json()["detail"]["blockers"]
    assert (await q(pool, "select messaging_mode as m from clinic"))[0]["m"] == "simulator"
    configure(monkeypatch)
    Graph(token_ok=False).install(client)
    assert (await client.put("/settings/messaging-mode", headers=h, json={"mode": "live"})).status_code == 409        # token rejected by Meta
    Graph().install(client)
    ok = await client.put("/settings/messaging-mode", headers=h, json={"mode": "live"})
    assert ok.status_code == 200 and (await q(pool, "select messaging_mode as m from clinic"))[0]["m"] == "live"
    back = await client.put("/settings/messaging-mode", headers=h, json={"mode": "simulator"})                          # the fallback needs no checks
    assert back.status_code == 200 and (await q(pool, "select messaging_mode as m from clinic"))[0]["m"] == "simulator"
    assert (await client.put("/settings/messaging-mode", headers=h, json={"mode": "pigeon"})).status_code == 422


async def test_submitting_templates_posts_meta_ready_definitions_and_reports_each(client, world, monkeypatch):
    assert (await client.post("/settings/templates/submit", headers=owner(world), json={})).status_code == 409          # not configured
    configure(monkeypatch)

    def answer(name):
        if name == "showup_recall":
            return httpx.Response(400, json={"error": {"code": 100, "error_subcode": 2388023, "message": "Content in this language already exists"}})
        if name == "showup_running_late":
            return httpx.Response(400, json={"error": {"code": 100, "message": "Invalid parameter"}})
        return httpx.Response(200, json={"id": "9", "status": "PENDING"})
    g = Graph(submit=answer).install(client)
    r = (await client.post("/settings/templates/submit", headers=owner(world), json={"language": "en"})).json()
    res = {x["name"]: x for x in r["results"]}
    assert len(res) == 9 and res["showup_day_before"]["result"] == "submitted"
    assert res["showup_recall"]["result"] == "already_exists" and res["showup_running_late"]["result"] == "error" and "Invalid parameter" in res["showup_running_late"]["detail"]
    sent = [json.loads(x.content) for x in g.requests if x.method == "POST"]
    d = next(x for x in sent if x["name"] == "showup_day_before")
    assert d["category"] == "UTILITY" and d["language"] == "en" and d["components"][0]["type"] == "BODY"
    assert d["components"][-1] == {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": t} for t in ("Confirm", "Reschedule", "Cancel")]}
    hi = (await client.post("/settings/templates/submit", headers=owner(world), json={"language": "hi"})).json()
    assert {x["name"]: x["result"] for x in hi["results"]}["showup_daily_summary"] == "skipped"                         # the doctor's summary has no Hindi text
    assert all(x["result"] != "skipped" for x in hi["results"] if x["name"] != "showup_daily_summary")                  # every patient template does


@pytest.mark.parametrize("given,saved", [("+91 25 1234 5678", "+912512345678"), ("0251 2345678", "+912512345678"), ("98765 43210", "+919876543210"),
                                         ("+1 415 555 0123", "+14155550123"), ("", None)])
async def test_the_clinic_phone_may_be_a_landline(client, world, given, saved):
    r = await client.patch("/settings/clinic", headers=owner(world), json={"phone": given})
    assert r.status_code == 200 and r.json()["phone"] == saved


async def test_saving_the_form_again_with_a_landline_already_stored_works(client, pool, world):
    await q(pool, "update clinic set phone='+912512345678' returning id")           # what the seed stores
    r = await client.patch("/settings/clinic", headers=owner(world), json={"phone": "+912512345678", "avg_ticket_value": 1300})
    assert r.status_code == 200 and r.json()["avg_ticket_value"] == 1300


@pytest.mark.parametrize("bad", ["12345", "abc", "+0123456789", "0123"])
async def test_the_clinic_phone_still_has_to_be_a_real_number(client, world, bad):
    assert (await client.patch("/settings/clinic", headers=owner(world), json={"phone": bad})).status_code == 422
