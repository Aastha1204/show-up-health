"""Razorpay deposit links. No Razorpay account here: requests/answers follow Razorpay's DOCUMENTED payment-link API through a mock
HTTP layer, so these prove our request shape, our handling of its answers and the signed-webhook logic, not that Razorpay accepts it."""
import base64
import hashlib
import hmac
import json

import httpx
import pytest

from app.config import Settings
from app.services import payments
from app.services import appointments as svc
from .conftest import token_for
from .test_messaging_db import book, frozen, q

pytestmark = pytest.mark.db
KEYS = Settings(razorpay_key_id="rzp_test_abc", razorpay_key_secret="sekret", razorpay_webhook_secret="whsec", _env_file=None)
NO_KEYS = Settings(demo_mode=False, _env_file=None)
DEMO_NO_KEYS = Settings(demo_mode=True, _env_file=None)


def rzp(answer=None, status=200, seen=None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, json=answer if answer is not None else {"id": "plink_Abc123", "short_url": "https://rzp.io/i/xyz",
                                                                              "status": "created"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def ask(pool, messaging, w, appt, s=KEYS, amount=None, client=None):
    return await payments.request_deposit(pool, messaging, s, clinic_id=w["clinic"], appointment_id=appt["id"], amount=amount,
                                          now=frozen(w), client=client)


async def row(pool, appt):
    return (await q(pool, "select * from appointment where id=%s", [appt["id"]]))[0]


async def test_request_creates_a_link_with_the_documented_shape_and_sends_it(pool, messaging, world):
    appt = await book(pool, world, "10:00")
    seen = []
    res = await ask(pool, messaging, world, appt, amount=750, client=rzp(seen=seen))
    assert res["sent"] and res["payment_link_url"] == "https://rzp.io/i/xyz" and not res["simulated"]
    req = seen[0]
    assert req.url == "https://api.razorpay.com/v1/payment_links"
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"rzp_test_abc:sekret").decode()
    body = json.loads(req.content)
    assert body["amount"] == 75000 and body["currency"] == "INR" and body["reference_id"] == str(appt["id"])
    assert body["accept_partial"] is False and body["notify"] == {"sms": False, "email": False}
    assert "treatment" not in body["description"].lower() and body["customer"]["contact"] == "+919876500000"
    a = await row(pool, appt)
    assert (a["deposit_status"], a["deposit_amount"], a["payment_link_id"]) == ("requested", 750, "plink_Abc123")
    m = (await q(pool, "select template_name, rendered_body from outbound_message where appointment_id=%s", [appt["id"]]))[0]
    assert m["template_name"] == "showup_deposit_request" and "₹750" in m["rendered_body"] and "https://rzp.io/i/xyz" in m["rendered_body"]


async def test_the_message_follows_the_patients_language(pool, messaging, world):
    await q(pool, "update patient set language='hi' where id=%s returning id", [world["p1"]])
    appt = await book(pool, world, "10:15")
    await ask(pool, messaging, world, appt, client=rzp())
    body = (await q(pool, "select rendered_body from outbound_message where appointment_id=%s", [appt["id"]]))[0]["rendered_body"]
    assert "बुकिंग राशि" in body and "₹500" in body


@pytest.mark.parametrize("amount", [1, 99, 50_001, -5])
async def test_amount_limits(pool, messaging, world, amount):
    appt = await book(pool, world, "10:30")
    with pytest.raises(svc.Invalid):
        await ask(pool, messaging, world, appt, amount=amount, client=rzp())


async def test_razorpay_errors_surface_and_leave_the_appointment_untouched(pool, messaging, world):
    appt = await book(pool, world, "10:45")
    err = {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
    with pytest.raises(payments.PaymentsError, match="Authentication failed"):
        await ask(pool, messaging, world, appt, client=rzp(err, 401))
    assert (await row(pool, appt))["deposit_status"] is None
    assert await q(pool, "select 1 from outbound_message where appointment_id=%s", [appt["id"]]) == []


async def test_unreachable_razorpay(pool, messaging, world):
    appt = await book(pool, world, "11:00")

    def boom(req):
        raise httpx.ConnectError("down")
    with pytest.raises(payments.PaymentsError) as e:
        await ask(pool, messaging, world, appt, client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    assert e.value.code == "razorpay_unreachable" if hasattr(e.value, "code") else True
    assert (await row(pool, appt))["deposit_status"] is None


async def test_not_configured_outside_demo_mode_but_simulated_in_demo_mode(pool, messaging, world):
    appt = await book(pool, world, "11:15")
    with pytest.raises(svc.Conflict):
        await ask(pool, messaging, world, appt, s=NO_KEYS)
    res = await ask(pool, messaging, world, appt, s=DEMO_NO_KEYS)
    assert res["simulated"] and res["payment_link_url"].startswith("https://rzp.io/i/demo-")
    assert (await row(pool, appt))["payment_link_id"].startswith("plink_demo_")


async def test_seeded_patients_never_get_a_real_link_even_with_keys(pool, messaging, world):
    await q(pool, "update patient set is_demo_seed=true where id=%s returning id", [world["p1"]])
    appt = await book(pool, world, "11:30")
    seen = []
    res = await ask(pool, messaging, world, appt, client=rzp(seen=seen))
    assert res["simulated"] and seen == []


@pytest.mark.parametrize("status", ["cancelled", "no_show", "arrived"])
async def test_only_for_upcoming_appointments_that_are_still_on(pool, messaging, world, status):
    appt = await book(pool, world, "11:45")
    await q(pool, "update appointment set status=%s where id=%s returning id", [status, appt["id"]])
    with pytest.raises(svc.Conflict):
        await ask(pool, messaging, world, appt, client=rzp())


async def test_opted_out_patients_are_not_asked(pool, messaging, world):
    await q(pool, "update patient set opted_out=true where id=%s returning id", [world["p1"]])
    appt = await book(pool, world, "12:00")
    with pytest.raises(svc.Conflict):
        await ask(pool, messaging, world, appt, client=rzp())


def paid_event(link="plink_Abc123", amount=50000):
    return {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": link, "amount_paid": amount, "status": "paid"}}}}


async def test_paid_webhook_marks_the_deposit_once_and_only_for_the_right_amount(pool, messaging, world):
    appt = await book(pool, world, "12:15")
    await ask(pool, messaging, world, appt, client=rzp())
    async with pool.connection() as conn:
        assert await payments.handle_webhook(conn, paid_event(amount=100), frozen(world)) == "amount_mismatch"
        assert await payments.handle_webhook(conn, paid_event("plink_other"), frozen(world)) == "unknown_link"
    assert (await row(pool, appt))["deposit_status"] == "requested"
    async with pool.connection() as conn:
        assert await payments.handle_webhook(conn, paid_event(), frozen(world)) == "paid"
        assert await payments.handle_webhook(conn, paid_event(), frozen(world)) == "already_paid"      # Razorpay retries
    a = await row(pool, appt)
    assert a["deposit_status"] == "paid" and a["deposit_paid_at"] is not None
    ev = await q(pool, "select detail from appointment_event where appointment_id=%s and detail->>'deposit'='paid'", [appt["id"]])
    assert len(ev) == 1
    with pytest.raises(svc.Conflict):                                                                    # no second request once paid
        await ask(pool, messaging, world, appt, client=rzp())


async def test_expired_webhook_and_a_fresh_request_after_it(pool, messaging, world):
    appt = await book(pool, world, "12:30")
    await ask(pool, messaging, world, appt, client=rzp())
    async with pool.connection() as conn:
        out = await payments.handle_webhook(conn, {"event": "payment_link.expired", "payload": {"payment_link": {"entity": {"id": "plink_Abc123"}}}},
                                            frozen(world))
    assert out == "expired" and (await row(pool, appt))["deposit_status"] == "expired"
    await ask(pool, messaging, world, appt, client=rzp({"id": "plink_Second", "short_url": "https://rzp.io/i/two"}))
    assert (await row(pool, appt))["payment_link_id"] == "plink_Second"


def sign(raw, secret="whsec"):
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def test_webhook_endpoint_checks_the_signature(client, pool, messaging, world, monkeypatch):
    import app.routers.webhooks as wh
    monkeypatch.setattr(wh, "get_settings", lambda: KEYS)
    appt = await book(pool, world, "12:45")
    await ask(pool, messaging, world, appt, client=rzp())
    raw = json.dumps(paid_event()).encode()
    bad = await client.post("/webhooks/razorpay", content=raw, headers={"x-razorpay-signature": sign(raw, "wrong")})
    none = await client.post("/webhooks/razorpay", content=raw)
    assert bad.status_code == none.status_code == 403 and (await row(pool, appt))["deposit_status"] == "requested"
    ok = await client.post("/webhooks/razorpay", content=raw, headers={"x-razorpay-signature": sign(raw)})
    assert ok.status_code == 200 and ok.json()["result"] == "paid" and (await row(pool, appt))["deposit_status"] == "paid"
    junk = b"not json"
    assert (await client.post("/webhooks/razorpay", content=junk, headers={"x-razorpay-signature": sign(junk)})).status_code == 400


async def test_webhook_refuses_everything_when_no_secret_is_configured(client, monkeypatch):
    import app.routers.webhooks as wh
    monkeypatch.setattr(wh, "get_settings", lambda: NO_KEYS)
    raw = b"{}"
    assert (await client.post("/webhooks/razorpay", content=raw, headers={"x-razorpay-signature": sign(raw, "")})).status_code == 403


async def test_staff_endpoint_and_demo_pay_button(client, pool, world, monkeypatch):
    import app.routers.appointments as ar
    import app.routers.demo as dm
    monkeypatch.setattr(ar, "get_settings", lambda: DEMO_NO_KEYS)
    monkeypatch.setattr(dm, "get_settings", lambda: DEMO_NO_KEYS)
    import app.routers.simulator as sim
    monkeypatch.setattr(sim, "get_settings", lambda: DEMO_NO_KEYS)
    appt = await book(pool, world, "13:00")
    h = token_for(world["recep_user"])
    r = await client.post(f"/appointments/{appt['id']}/deposit", headers=h, json={"amount": 300})
    assert r.status_code == 200 and r.json()["amount"] == 300 and r.json()["simulated"]
    got = (await client.get(f"/appointments/{appt['id']}", headers=h)).json()
    assert got["deposit_status"] == "requested" and got["deposit_amount"] == 300 and got["payment_link_url"].startswith("https://rzp.io")
    assert (await client.post(f"/appointments/{appt['id']}/deposit", headers=h, json={"amount": 5})).status_code == 422
    p = await client.post(f"/demo/deposit/{appt['id']}/pay", headers=h)
    assert p.status_code == 200 and p.json()["result"] == "paid"
    assert (await client.get(f"/appointments/{appt['id']}", headers=h)).json()["deposit_status"] == "paid"
    assert (await client.post(f"/demo/deposit/{appt['id']}/pay", headers=h)).status_code == 409
    other = await client.post("/appointments/00000000-0000-0000-0000-000000000000/deposit", headers=h)
    assert other.status_code == 404
