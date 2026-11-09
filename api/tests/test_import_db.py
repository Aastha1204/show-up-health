"""Import end to end: preview validates every row without writing; commit re-validates and creates only what is valid."""
import pytest

from app.routers import imports as imports_router
from app.services import importer as imp
from .conftest import token_for
from .test_messaging_db import book, q

pytestmark = pytest.mark.db


def csv_for(w) -> str:
    d, iso = w["day"], w["day"].isoformat()
    dmy = d.strftime("%d/%m/%Y")
    return "\n".join([
        "Patient,Mobile,Doctor,Date,Time,Service,Language",
        f"Asha Rao,98765 11111,Dr. Neha,{iso},10:00,Consultation,",                 # 1  ok
        f"Bad Phone,12345,Dr. Neha,{iso},10:30,Consultation,",                      # 2  phone
        f"Ghost Doc,98765 22222,Dr. Nobody,{iso},11:00,Consultation,",              # 3  doctor
        "Bad Date,98765 33333,Dr. Neha,notadate,11:00,Consultation,",               # 4  date
        f"Off Hours,98765 44444,Dr. Neha,{iso},14:00,Consultation,",                # 5  outside working hours
        "Long Ago,98765 55555,Dr. Neha,2020-01-06,10:00,Consultation,",             # 6  past
        f"Same Slot,98765 66666,Dr. Neha,{iso},10:00,Consultation,",                # 7  overlaps row 1
        f"Arjun Ok,98765 77777,Dr. Arjun Mehta,{dmy},10:30 AM,Root canal session,",   # 8  ok (other date format, 60 min)
        f"Clash Existing,98765 88888,Dr. Neha,{iso},12:00,Consultation,",           # 9  overlaps an existing booking
        f"Odd Treatment,98765 99999,Dr. Neha,{iso},12:30,Teleport,",                # 10 treatment
        f"Warn Lang,98765 12121,Dr. Neha,{iso},13:00,Consultation,Klingon",         # 11 ok, with a warning
    ]) + "\n"


async def setup(pool, w):
    await book(pool, w, "12:00", patient="p3")                                      # the existing booking row 9 clashes with


async def preview(client, w, text, mapping=None, **extra):
    body = {"csv_text": text, **({"mapping": mapping} if mapping else {}), **extra}
    return await client.post("/import/preview", json=body, headers=token_for(w["recep_user"]))


async def test_preview_validates_every_row_and_writes_nothing(client, pool, world):
    await setup(pool, world)
    before = (await q(pool, "select count(*) as n from appointment"))[0]["n"], (await q(pool, "select count(*) as n from patient"))[0]["n"]
    j = (await preview(client, world, csv_for(world))).json()
    assert j["needs_mapping"] is False and j["total_rows"] == 11
    assert j["mapping"] == {"patient_name": "Patient", "phone": "Mobile", "doctor": "Doctor", "date": "Date", "time": "Time",
                            "treatment": "Service", "language": "Language", "note": None}
    assert j["counts"] == {"total": 11, "ok": 3, "errors": 8, "warnings": 1}
    rows = {r["row"]: r for r in j["rows"]}
    assert [n for n, r in rows.items() if r["ok"]] == [1, 8, 11]
    err = lambda n: " ".join(rows[n]["errors"])  # noqa: E731
    assert "Phone" in err(2) and "No doctor matches" in err(3) and "not a date" in err(4)
    assert "working hours" in err(5) and "past" in err(6) and "Overlaps row 1" in err(7)
    assert "existing appointment" in err(9) and "No treatment matches" in err(10)
    assert "not recognised" in rows[11]["warnings"][0]
    assert rows[8]["summary"]["doctor"] == "Dr. Arjun" and rows[8]["summary"]["treatment"] == "Root canal session"
    assert rows[1]["summary"]["phone"] == "+919876511111"
    after = (await q(pool, "select count(*) as n from appointment"))[0]["n"], (await q(pool, "select count(*) as n from patient"))[0]["n"]
    assert after == before                                                          # a preview never writes


async def test_preview_asks_for_a_mapping_when_headers_are_unrecognisable(client, world):
    j = (await preview(client, world, "Col A,Col B,Col C\nx,y,z\n")).json()
    assert j["needs_mapping"] is True and j["rows"] == [] and j["headers"] == ["Col A", "Col B", "Col C"]
    ok = (await preview(client, world, "Col A,Col B,Col C,Col D,Col E\nAsha,98765 11111,Neha,2099-01-05,10:00\n",
                        mapping={"patient_name": "Col A", "phone": "Col B", "doctor": "Col C", "date": "Col D", "time": "Col E"})).json()
    assert ok["needs_mapping"] is False and len(ok["rows"]) == 1
    bogus = (await preview(client, world, "A,B\n1,2\n", mapping={"patient_name": "Nope"})).json()    # unknown column names are ignored
    assert bogus["mapping"]["patient_name"] is None


async def test_commit_creates_only_valid_rows_with_import_consent_and_no_confirmation_blast(client, pool, world):
    await setup(pool, world)
    text = csv_for(world)
    mapping = (await preview(client, world, text)).json()["mapping"]
    r = await client.post("/import/commit", headers=token_for(world["recep_user"]),
                          json={"csv_text": text, "mapping": mapping, "consent_confirmed": True, "send_confirmations": False})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["created"] == 3 and len(j["skipped"]) == 8 and j["counts"]["ok"] == 3
    appts = await q(pool, """select p.name, p.consent_source, p.language, p.opted_out, a.source, a.status, a.duration_min, a.id
                             from appointment a join patient p on p.id=a.patient_id where a.source='import' order by p.name""")
    assert [(a["name"], a["consent_source"], a["source"], a["status"]) for a in appts] == [
        ("Arjun Ok", "import", "import", "pending"), ("Asha Rao", "import", "import", "pending"), ("Warn Lang", "import", "import", "pending")]
    assert [a["duration_min"] for a in appts] == [60, 20, 20] and appts[2]["language"] == "en"
    for a in appts:                                                                 # reminders yes, a confirmation blast no
        types = {x["type"] for x in await q(pool, "select type from reminder_job where appointment_id=%s", [a["id"]])}
        assert "confirmation" not in types and {"day_before", "two_hour"} <= types


async def test_commit_can_send_confirmations_when_asked(client, pool, world):
    text = "Patient,Phone,Doctor,Date,Time\nAsha Rao,98765 11111,Dr. Neha,%s,10:00\n" % world["day"].isoformat()
    mapping = (await preview(client, world, text)).json()["mapping"]
    r = await client.post("/import/commit", headers=token_for(world["recep_user"]),
                          json={"csv_text": text, "mapping": mapping, "consent_confirmed": True, "send_confirmations": True})
    assert r.json()["created"] == 1
    assert "confirmation" in {x["type"] for x in await q(pool, "select type from reminder_job")}


async def test_commit_requires_the_consent_confirmation(client, pool, world):
    text = "Patient,Phone,Doctor,Date,Time\nAsha Rao,98765 11111,Dr. Neha,%s,10:00\n" % world["day"].isoformat()
    mapping = (await preview(client, world, text)).json()["mapping"]
    for confirmed in (False, None):
        body = {"csv_text": text, "mapping": mapping, **({"consent_confirmed": confirmed} if confirmed is not None else {})}
        r = await client.post("/import/commit", headers=token_for(world["recep_user"]), json=body)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required"
    assert (await q(pool, "select count(*) as n from appointment"))[0]["n"] == 0


async def test_reimporting_the_same_file_creates_nothing_new(client, pool, world):
    text = "Patient,Phone,Doctor,Date,Time\nAsha Rao,98765 11111,Dr. Neha,%s,10:00\n" % world["day"].isoformat()
    mapping = (await preview(client, world, text)).json()["mapping"]
    body = {"csv_text": text, "mapping": mapping, "consent_confirmed": True}
    assert (await client.post("/import/commit", headers=token_for(world["recep_user"]), json=body)).json()["created"] == 1
    again = (await client.post("/import/commit", headers=token_for(world["recep_user"]), json=body)).json()
    assert again["created"] == 0 and again["skipped"][0]["reason"] == "Already booked (this patient at this time)"
    assert (await q(pool, "select count(*) as n from appointment"))[0]["n"] == 1


async def test_commit_trusts_nothing_from_the_preview(client, pool, world):
    text = "Patient,Phone,Doctor,Date,Time\nAsha Rao,98765 11111,Dr. Neha,%s,10:00\n" % world["day"].isoformat()
    mapping = (await preview(client, world, text)).json()["mapping"]
    await book(pool, world, "10:00", patient="p3")                                  # someone takes the slot AFTER the preview
    r = await client.post("/import/commit", headers=token_for(world["recep_user"]),
                          json={"csv_text": text, "mapping": mapping, "consent_confirmed": True})
    assert r.json()["created"] == 0 and "existing appointment" in r.json()["skipped"][0]["reason"]


async def test_commit_with_an_incomplete_mapping_is_refused(client, world):
    r = await client.post("/import/commit", headers=token_for(world["recep_user"]),
                          json={"csv_text": "A,B\n1,2\n", "mapping": {"patient_name": "A"}, "consent_confirmed": True})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "mapping_incomplete"


async def test_source_is_required_and_exclusive(client, world):
    h = token_for(world["recep_user"])
    assert (await client.post("/import/preview", headers=h, json={})).json()["detail"]["code"] == "source_required"
    both = await client.post("/import/preview", headers=h, json={"csv_text": "a,b\n1,2", "sheet_url": "https://docs.google.com/spreadsheets/d/e/x/pub"})
    assert both.status_code == 422
    assert (await client.post("/import/preview", json={"csv_text": "a,b\n1,2"})).status_code == 401


async def test_preview_from_a_google_sheet_link_returns_the_csv_to_commit(client, world, monkeypatch):
    seen = []

    async def fake(url, client=None):
        seen.append(url)
        return "Patient,Phone,Doctor,Date,Time\nAsha Rao,98765 11111,Dr. Neha,%s,10:00\n" % world["day"].isoformat()
    monkeypatch.setattr(imports_router.imp, "fetch_sheet_csv", fake)
    j = (await client.post("/import/preview", headers=token_for(world["recep_user"]),
                           json={"sheet_url": "https://docs.google.com/spreadsheets/d/e/x/pub?output=csv"})).json()
    assert seen and j["counts"]["ok"] == 1 and j["csv_text"].startswith("Patient,Phone")      # commit re-sends exactly this text


async def test_a_bad_sheet_link_is_a_clear_error_not_a_fetch(client, world):
    r = await client.post("/import/preview", headers=token_for(world["recep_user"]), json={"sheet_url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_sheet_url"


async def test_a_file_over_the_row_limit_is_refused(client, world):
    big = "Patient,Phone,Doctor,Date,Time\n" + "\n".join(f"P{i},9876543{i:04d},Dr. Neha,2099-01-05,10:00" for i in range(imp.MAX_ROWS + 1))
    r = await preview(client, world, big)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_many_rows"
