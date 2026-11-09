"""Private iCalendar feed: content, privacy, token handling."""
from datetime import datetime, timezone

import pytest

from app.ratelimit import client_ip  # noqa: F401  (import check)
from app.routers import calendar as cal
from app.services import ical
from .conftest import token_for
from .test_messaging_db import book, q

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)


def unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


def test_pure_builder_escapes_folds_and_skips_cancelled():
    rows = [{"id": "a1", "start_at": NOW, "end_at": NOW.replace(hour=6), "status": "confirmed", "updated_at": NOW, "patient_name": "Rao, Priya; Jr"},
            {"id": "a2", "start_at": NOW, "end_at": NOW.replace(hour=6), "status": "cancelled", "updated_at": NOW, "patient_name": "Gone"},
            {"id": "a3", "start_at": NOW, "end_at": NOW.replace(hour=6), "status": "no_show", "updated_at": NOW, "patient_name": "Gone2"},
            {"id": "a4", "start_at": NOW, "end_at": NOW.replace(hour=6), "status": "pending", "updated_at": NOW, "patient_name": "नमिता " * 30}]
    out = ical.build("Dr. Neha", "SmileCare", rows, NOW)
    assert out.startswith("BEGIN:VCALENDAR\r\n") and out.endswith("END:VCALENDAR\r\n")
    assert out.count("BEGIN:VEVENT") == 2 and "Gone" not in out
    assert "SUMMARY:Rao\\, Priya\\; Jr" in out and "STATUS:TENTATIVE" in out and "DTSTART:20260109T" not in out
    assert "DTSTART:20261109T050000Z" in out
    assert all(len(l.encode("utf-8")) <= 75 for l in out.split("\r\n")), "lines must fold at 75 octets"
    assert "नमिता" in "".join(unfold(out))                                          # folding never splits a character


async def test_feed_serves_only_that_doctors_live_appointments_without_private_details(client, pool, world):
    mine = await book(pool, world, "10:00", doctor="neha")
    await book(pool, world, "10:00", doctor="arjun", patient="p2")
    gone = await book(pool, world, "11:00", doctor="neha", patient="p3")
    await q(pool, "update appointment set status='cancelled' where id=%s returning id", [gone["id"]])
    await q(pool, "update appointment set internal_note='SECRET NOTE' where id=%s returning id", [mine["id"]])
    token = (await q(pool, "select calendar_token from doctor where id=%s", [world["neha"]]))[0]["calendar_token"]
    r = await client.get(f"/calendar/{token}.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert r.headers["x-robots-tag"] == "noindex"
    body = r.text
    assert body.count("BEGIN:VEVENT") == 1 and f"UID:{mine['id']}@showup" in body and "SUMMARY:Patient 1" in body
    for leak in ("Patient 2", "Patient 3", "SECRET NOTE", "+91987650000", "Consultation", "Root canal"):
        assert leak not in body, leak


@pytest.mark.parametrize("token", ["x" * 64, "short", "0" * 64, "../etc/passwd", "A" * 64])
async def test_bad_or_unknown_tokens_are_404(client, token):
    cal.feed_limiter.reset()
    assert (await client.get(f"/calendar/{token}.ics")).status_code == 404


async def test_inactive_doctors_have_no_feed_and_resetting_kills_the_old_link(client, pool, world):
    cal.feed_limiter.reset()
    old = (await q(pool, "select calendar_token from doctor where id=%s", [world["neha"]]))[0]["calendar_token"]
    assert len(old) == 64 and (await client.get(f"/calendar/{old}.ics")).status_code == 200
    r = await client.post(f"/settings/doctors/{world['neha']}/calendar/reset", headers=token_for(world["owner_user"]))
    new = r.json()["calendar_token"]
    assert r.status_code == 200 and new != old and len(new) == 64
    assert (await client.get(f"/calendar/{old}.ics")).status_code == 404
    assert (await client.get(f"/calendar/{new}.ics")).status_code == 200
    await q(pool, "update doctor set active=false where id=%s returning id", [world["neha"]])
    assert (await client.get(f"/calendar/{new}.ics")).status_code == 404


async def test_reset_is_owner_only_and_clinic_scoped(client, world):
    assert (await client.post(f"/settings/doctors/{world['neha']}/calendar/reset", headers=token_for(world["recep_user"]))).status_code == 403
    assert (await client.post(f"/settings/doctors/{world['neha']}/calendar/reset")).status_code == 401
    assert (await client.post("/settings/doctors/00000000-0000-0000-0000-000000000000/calendar/reset",
                              headers=token_for(world["owner_user"]))).status_code == 404


async def test_settings_lists_the_token_for_owners_only_and_reference_never_does(client, world):
    s = (await client.get("/settings", headers=token_for(world["owner_user"]))).json()
    assert all(len(d["calendar_token"]) == 64 for d in s["doctors"])
    ref = (await client.get("/reference", headers=token_for(world["recep_user"]))).text
    assert "calendar_token" not in ref


async def test_the_feed_is_rate_limited(client):
    cal.feed_limiter.reset()
    codes = [(await client.get("/calendar/" + "a" * 64 + ".ics")).status_code for _ in range(32)]
    assert codes[:30] == [404] * 30 and 429 in codes[30:]
    cal.feed_limiter.reset()
