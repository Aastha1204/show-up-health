"""Live transport. There is no Meta account here, so these tests replay Meta's DOCUMENTED request/response shapes through a
mock HTTP layer. They prove our requests have the right shape and our handling of Meta's answers is right; they cannot prove
Meta accepts the real thing (that needs a real WhatsApp Business account and approved templates)."""
import json
import uuid
from datetime import timedelta

import httpx
import pytest

from app import jobs
from app.config import Settings
from app.messaging import meta_payloads as mp, templates, webhook
from app.messaging.service import MessagingService, Outgoing
from app.messaging.transport import Delivery, LiveTransport, TransportError, build_payload
from app.services import reschedule
from .test_messaging_db import book, frozen, job_rows, q

SETTINGS = Settings(wa_access_token="TOKEN-123", wa_phone_number_id="1055550001", wa_api_version="v21.0", _env_file=None)
PHONE = "+919876500000"


def delivery(kind="text", **kw):
    base = dict(outbound_id=uuid.uuid4(), to_phone=PHONE, kind=kind, body="Hello there", template_name=None, language="en",
                footer=None, buttons=None, list=None, is_demo_seed=False, params=None)
    base.update(kw)
    return Delivery(**base)


def rendered_delivery(name, ids=None):
    r = templates.render(name, "en", templates.sample_values(name), ids=ids or {"appointment_id": "A1", "patient_id": "P1", "offer_id": "O1"})
    return delivery("template", body=r.body, template_name=name, language=r.language, footer=r.footer, buttons=r.buttons or None, params=r.params)


class Recorder:
    """A fake Graph API: records every request and answers from a script (a list of httpx.Response or exceptions)."""
    def __init__(self, *script):
        self.script, self.requests = list(script), []

    async def __call__(self, request: httpx.Request):
        self.requests.append(request)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def body(self, i=0):
        return json.loads(self.requests[i].content)


def ok(wamid="wamid.HBgLOTE5ODc2NTAwMDAw"):
    return httpx.Response(200, json={"messaging_product": "whatsapp", "contacts": [{"input": "919876500000", "wa_id": "919876500000"}],
                                     "messages": [{"id": wamid}]})


def meta_error(code, message="boom", status=400, headers=None):
    return httpx.Response(status, json={"error": {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "AbC"}}, headers=headers)


def live(rec, sleeps=None):
    async def nap(s):
        (sleeps if sleeps is not None else []).append(s)
    return LiveTransport(client=rec.client(), sleep=nap, settings=lambda: SETTINGS)


# ───────────────────────── payload shapes ─────────────────────────
def test_text_payload():
    assert build_payload(delivery("text", body="Confirmed ✅ See you on Mon 9 Nov at 10:30 AM.")) == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": "919876500000", "type": "text",
        "text": {"preview_url": False, "body": "Confirmed ✅ See you on Mon 9 Nov at 10:30 AM."}}


def test_template_payload_with_quick_reply_buttons():
    p = build_payload(rendered_delivery("showup_day_before"))
    assert p["to"] == "919876500000" and p["type"] == "template"                         # no "+" in the recipient
    tpl = p["template"]
    assert tpl["name"] == "showup_day_before" and tpl["language"] == {"code": "en"}
    body, *buttons = tpl["components"]
    assert body == {"type": "body", "parameters": [{"type": "text", "text": t} for t in
                    ["Priya", "SmileCare Dental Clinic", "Tue 10 Nov", "10:30 AM", "Dr. Neha Kulkarni"]]}
    assert buttons == [{"type": "button", "sub_type": "quick_reply", "index": str(i),
                        "parameters": [{"type": "payload", "payload": f"{v}:A1"}]} for i, v in enumerate(["CONFIRM", "RESCHEDULE", "CANCEL"])]
    assert "footer" not in json.dumps(p) and "Confirm" not in json.dumps(p)              # labels/footer live in the approved template


def test_template_without_buttons_has_only_a_body_component():
    comps = build_payload(rendered_delivery("showup_two_hour"))["template"]["components"]
    assert [c["type"] for c in comps] == ["body"] and len(comps[0]["parameters"]) == 5


@pytest.mark.parametrize("name", sorted(templates.load_all()))
def test_every_template_sends_exactly_its_variables(name):
    t = templates.get_template(name)
    p = build_payload(rendered_delivery(name))["template"]
    assert len(p["components"][0]["parameters"]) == len(t["variables"])                  # Meta rejects a count mismatch (132000)
    assert sum(c["type"] == "button" for c in p["components"]) == len(t["buttons"])
    assert all("\n" not in x["text"] and x["text"] for x in p["components"][0]["parameters"])


def test_template_in_another_language_uses_that_code():
    assert build_payload(delivery("template", template_name="showup_recall", language="hi", params=["a", "6", "b"]))["template"]["language"] == {"code": "hi"}


def test_interactive_list_payload_from_the_real_reschedule_list():
    from app.timeutil import ist_to_utc
    from datetime import time, date
    opts = [reschedule.SlotOption(uuid.uuid4(), "Dr. Neha", ist_to_utc(date(2030, 1, 7) + timedelta(days=i), time(10 + j, 0))) for i in range(3) for j in range(3)]
    lst = reschedule.build_list(uuid.uuid4(), opts)
    p = build_payload(delivery("interactive_list", body=reschedule.BODY, list=lst))
    i = p["interactive"]
    assert p["type"] == "interactive" and i["type"] == "list" and i["body"] == {"text": reschedule.BODY}
    assert i["header"] == {"type": "text", "text": "Pick a new time"} and i["action"]["button"] == "See slots"
    rows = [r for s in i["action"]["sections"] for r in s["rows"]]
    assert len(rows) == 10 and rows[-1]["title"] == "None of these work" and "description" not in rows[-1]   # empty description omitted
    assert rows[0]["description"] == "Dr. Neha" and all(len(r["title"]) <= 24 and len(r["id"]) <= 200 for r in rows)
    assert [s["title"] for s in i["action"]["sections"]][-1] == "Other"


def test_interactive_buttons_payload_and_unknown_kind():
    p = build_payload(delivery("interactive_buttons", body="Pick one", buttons=[{"id": "A", "text": "Yes"}, {"id": "B", "text": "No"}]))
    assert p["interactive"] == {"type": "button", "body": {"text": "Pick one"}, "action": {"buttons": [
        {"type": "reply", "reply": {"id": "A", "title": "Yes"}}, {"type": "reply", "reply": {"id": "B", "title": "No"}}]}}
    with pytest.raises(TransportError):
        build_payload(delivery("carrier_pigeon"))


# ───────────────────────── delivery and retries ─────────────────────────
async def test_successful_send_hits_the_right_url_with_the_token_and_returns_the_wamid():
    rec = Recorder(ok())
    wamid = await live(rec).deliver(delivery("text"))
    assert wamid == "wamid.HBgLOTE5ODc2NTAwMDAw" and len(rec.requests) == 1
    r = rec.requests[0]
    assert str(r.url) == "https://graph.facebook.com/v21.0/1055550001/messages" and r.method == "POST"
    assert r.headers["authorization"] == "Bearer TOKEN-123" and rec.body()["to"] == "919876500000"


async def test_graph_version_comes_from_configuration():
    rec = Recorder(ok())
    t = LiveTransport(client=rec.client(), settings=lambda: Settings(wa_access_token="t", wa_phone_number_id="9", wa_api_version="v23.0", _env_file=None))
    await t.deliver(delivery("text"))
    assert "/v23.0/9/messages" in str(rec.requests[0].url)


async def test_refuses_without_credentials_and_for_seeds_without_any_http_call():
    rec = Recorder(ok())
    bare = LiveTransport(client=rec.client(), settings=lambda: Settings(_env_file=None, wa_access_token="", wa_phone_number_id=""))
    with pytest.raises(TransportError) as e:
        await bare.deliver(delivery("text"))
    assert e.value.code == "live_not_configured"
    with pytest.raises(TransportError) as e:
        await live(rec).deliver(delivery("text", is_demo_seed=True))
    assert e.value.code == "demo_seed" and rec.requests == []


async def test_rate_limits_and_server_errors_are_retried_then_succeed():
    sleeps = []
    rec = Recorder(httpx.Response(429, headers={"retry-after": "2"}, json={}), httpx.Response(503, json={}), ok())
    assert await live(rec, sleeps).deliver(delivery("text")) == "wamid.HBgLOTE5ODc2NTAwMDAw"
    assert len(rec.requests) == 3 and sleeps == [2.0, 1.5]                                # honours Retry-After, else backs off


async def test_meta_throttle_codes_are_transient_even_on_http_400():
    rec = Recorder(meta_error(130429, "Rate limit hit"), ok())
    assert await live(rec).deliver(delivery("text"))
    assert len(rec.requests) == 2


async def test_gives_up_after_three_tries_and_marks_the_failure_transient():
    sleeps = []
    rec = Recorder(httpx.Response(503, json={}))
    with pytest.raises(TransportError) as e:
        await live(rec, sleeps).deliver(delivery("text"))
    assert len(rec.requests) == 3 and e.value.transient is True and e.value.code == "http_503" and len(sleeps) == 2


@pytest.mark.parametrize("code,needle", [(131047, "24-hour"), (190, "access token"), (131030, "allowed recipient list"),
                                         (132001, "does not exist"), (131026, "not on WhatsApp"), (132012, "wrong format")])
async def test_permanent_meta_errors_are_not_retried_and_keep_code_and_a_plain_explanation(code, needle):
    rec = Recorder(meta_error(code, f"(#{code}) Meta says no"))
    with pytest.raises(TransportError) as e:
        await live(rec).deliver(delivery("text"))
    assert len(rec.requests) == 1 and e.value.transient is False
    assert e.value.code == f"meta_{code}" and f"(#{code}) Meta says no" in str(e.value) and needle in str(e.value)


async def test_connection_failures_are_retried_because_nothing_was_sent():
    rec = Recorder(httpx.ConnectError("refused"), httpx.ConnectTimeout("slow"), ok())
    assert await live(rec).deliver(delivery("text"))
    rec2 = Recorder(httpx.ConnectError("refused"))
    with pytest.raises(TransportError) as e:
        await live(rec2).deliver(delivery("text"))
    assert e.value.code == "network" and e.value.transient is True and len(rec2.requests) == 3


async def test_a_read_timeout_is_never_retried_because_it_may_have_been_delivered():
    rec = Recorder(httpx.ReadTimeout("no answer"), ok())
    with pytest.raises(TransportError) as e:
        await live(rec).deliver(delivery("text"))
    assert len(rec.requests) == 1                                                         # a retry could message the patient twice
    assert e.value.code == "timeout_ambiguous" and e.value.transient is False and "may or may not" in str(e.value)


async def test_a_200_without_a_message_id_is_an_error_not_a_success():
    for resp in (httpx.Response(200, json={}), httpx.Response(200, text="<html>"), httpx.Response(200, json={"messages": []})):
        with pytest.raises(TransportError) as e:
            await live(Recorder(resp)).deliver(delivery("text"))
        assert e.value.code == "bad_response"


# ───────────────────────── through the real pipeline, in Live mode ─────────────────────────
pytestmark_db = pytest.mark.db


def live_service(pool, rec, sleeps=None):
    return MessagingService(pool, sim_delays=None, live=live(rec, sleeps))


@pytest.mark.db
async def test_a_live_confirmation_goes_out_as_a_template_and_status_webhooks_update_it(pool, world):
    await q(pool, "update clinic set messaging_mode='live' where id=%s returning id", [world["clinic"]])
    rec = Recorder(ok("wamid.LIVE0001"))
    svc = live_service(pool, rec)
    now = frozen(world)
    a = await book(pool, world, "10:30", now=now)
    counts = await jobs.tick(pool, svc, now + timedelta(seconds=30))
    assert counts["sent"] == 1
    sent = rec.body()
    assert sent["to"] == "919876500000" and sent["template"]["name"] == "showup_booking_confirmation"
    assert [p["text"] for p in sent["template"]["components"][0]["parameters"]][:3] == ["Patient", "Test Clinic", sent["template"]["components"][0]["parameters"][2]["text"]]
    assert len(sent["template"]["components"][0]["parameters"]) == 6
    (m,) = await q(pool, "select transport, status, wa_message_id, appointment_id from outbound_message")
    assert (m["transport"], m["status"], m["wa_message_id"], m["appointment_id"]) == ("live", "sent", "wamid.LIVE0001", a["id"])
    # Meta's status webhooks: sent -> delivered -> read, never backwards
    for st in ("delivered", "read", "delivered"):
        await webhook.handle_payload(pool, mp.status_payload("wamid.LIVE0001", st, PHONE))
    assert (await q(pool, "select status from outbound_message"))[0]["status"] == "read"
    await webhook.handle_payload(pool, mp.status_payload("wamid.LIVE0001", "failed", PHONE, [{"code": 131047, "title": "Re-engagement message"}]))
    row = (await q(pool, "select status, error from outbound_message"))[0]
    assert row["status"] == "failed" and "131047" in row["error"]


@pytest.mark.db
async def test_a_meta_rejection_fails_the_job_with_the_code_and_is_not_retried(pool, world):
    await q(pool, "update clinic set messaging_mode='live' where id=%s returning id", [world["clinic"]])
    rec = Recorder(meta_error(131030, "(#131030) Recipient phone number not in allowed list"))
    svc = live_service(pool, rec)
    now = frozen(world)
    a = await book(pool, world, "10:30", now=now)
    counts = await jobs.tick(pool, svc, now + timedelta(seconds=30))
    assert counts["failed"] == 1 and len(rec.requests) == 1
    conf = next(j for j in await job_rows(pool, a["id"]) if j["type"] == "confirmation")
    assert (conf["status"], conf["skip_reason"]) == ("failed", "meta_131030")
    (m,) = await q(pool, "select status, error from outbound_message")
    assert m["status"] == "failed" and "meta_131030" in m["error"] and "allowed recipient list" in m["error"]


@pytest.mark.db
async def test_a_transient_failure_is_retried_by_the_scheduler_later_not_lost(pool, world):
    await q(pool, "update clinic set messaging_mode='live' where id=%s returning id", [world["clinic"]])
    rec = Recorder(httpx.Response(503, json={}))
    svc = live_service(pool, rec)
    now = frozen(world)
    a = await book(pool, world, "10:30", now=now)
    counts = await jobs.tick(pool, svc, now + timedelta(seconds=30))
    assert counts["retry"] == 1 and len(rec.requests) == 3                                # 3 quick tries inside the transport...
    conf = next(j for j in await job_rows(pool, a["id"]) if j["type"] == "confirmation")
    assert conf["status"] == "scheduled" and conf["due_at"] > now + timedelta(seconds=30)  # ...then the job itself is rescheduled
    rec.script = [ok("wamid.LATER")]                                                       # Meta recovers
    assert (await jobs.tick(pool, svc, now + timedelta(minutes=3)))["sent"] >= 1
    assert (await q(pool, "select count(*) as n from outbound_message where status='sent' and wa_message_id='wamid.LATER'"))[0]["n"] == 1


@pytest.mark.db
async def test_seeded_patients_never_reach_the_graph_api_even_in_live_mode(pool, world):
    await q(pool, "update clinic set messaging_mode='live' where id=%s returning id", [world["clinic"]])
    await q(pool, "update patient set is_demo_seed=true where id=%s returning id", [world["p1"]])
    rec = Recorder(ok())
    svc = live_service(pool, rec)
    now = frozen(world)
    await book(pool, world, "10:30", now=now)
    await jobs.tick(pool, svc, now + timedelta(seconds=30))
    assert rec.requests == []                                                              # not one HTTP request left the building
    assert (await q(pool, "select transport, status from outbound_message"))[0] == {"transport": "simulator", "status": "sent"}


@pytest.mark.db
async def test_free_text_and_lists_use_the_session_message_shapes(pool, world):
    await q(pool, "update clinic set messaging_mode='live' where id=%s returning id", [world["clinic"]])
    rec = Recorder(ok("wamid.S1"), ok("wamid.S2"))
    svc = live_service(pool, rec)
    clinic = (await q(pool, "select * from clinic"))[0]
    patient = (await q(pool, "select * from patient where id=%s", [world["p1"]]))[0]
    await webhook.handle_payload(pool, mp.text_payload(PHONE, "hi", msg_id="in-live-1"), svc)      # opens the 24 h window (+ an auto-ack)
    base = len(rec.requests)
    async with pool.connection() as conn:
        r1 = await svc.send(conn, clinic, Outgoing("text", "Yes, 10:00 works."), patient=patient, proactive=False)
        assert r1.outcome == "sent"
    assert rec.body(base)["type"] == "text" and rec.body(base)["text"]["body"] == "Yes, 10:00 works."
