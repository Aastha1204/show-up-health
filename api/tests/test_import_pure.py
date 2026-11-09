"""Importer: parsing, column guessing, tolerant dates/times, doctor matching, and the Google Sheet fetch's SSRF defences."""
from datetime import date, time

import httpx
import pytest

from app.services import importer as imp

DOCTORS = [{"id": 1, "name": "Dr. Neha Kulkarni"}, {"id": 2, "name": "Dr. Arjun Mehta"}, {"id": 3, "name": "Dr. Neha Sharma"}]
TTS = [{"id": 1, "name": "Consultation", "duration_min": 20}, {"id": 2, "name": "Scaling & polishing", "duration_min": 30},
       {"id": 3, "name": "Root canal session", "duration_min": 60}]


# ───────── parsing ─────────
def test_parses_a_bom_prefixed_comma_file_and_skips_blank_lines():
    h, rows = imp.parse_csv("﻿Name,Phone\nAsha,98765 43210\n\n,\nBala,98765 11111\n")
    assert h == ["Name", "Phone"] and [r["Name"] for r in rows] == ["Asha", "Bala"]


@pytest.mark.parametrize("sep", [";", "\t", "|"])
def test_detects_other_delimiters(sep):
    h, rows = imp.parse_csv(f"Name{sep}Phone{sep}Doctor\nAsha{sep}98765 43210{sep}Neha\n")
    assert h == ["Name", "Phone", "Doctor"] and rows[0]["Doctor"] == "Neha"


def test_quoted_commas_and_short_rows():
    h, rows = imp.parse_csv('Name,Note,Phone\n"Rao, Asha","likes mornings, early",98765 43210\nShort,only two\n')
    assert rows[0]["Name"] == "Rao, Asha" and rows[0]["Note"] == "likes mornings, early"
    assert rows[1]["Phone"] == ""                                                 # missing cells become blank, not an error


@pytest.mark.parametrize("text,code", [("", "empty"), ("   \n  ", "empty"), ("Name,Phone\n", "no_rows"), ("Name,Name\nA,B\n", "bad_headers"),
                                       ("Name,\nA,B\n", "bad_headers")])
def test_bad_files_are_refused_with_a_reason(text, code):
    with pytest.raises(imp.ImportError_) as e:
        imp.parse_csv(text)
    assert e.value.code == code


def test_size_and_row_limits():
    big = "Name,Phone\n" + "\n".join(f"P{i},9876543{i:04d}" for i in range(imp.MAX_ROWS + 1))
    with pytest.raises(imp.ImportError_) as e:
        imp.parse_csv(big)
    assert e.value.code == "too_many_rows" and e.value.status == 413
    assert len(imp.parse_csv("Name,Phone\n" + "\n".join(f"P{i},9876543{i:04d}" for i in range(imp.MAX_ROWS)))[1]) == imp.MAX_ROWS
    with pytest.raises(imp.ImportError_) as e:
        imp.parse_csv("Name,Phone\n" + "x" * (imp.MAX_BYTES + 10))
    assert e.value.code == "too_large"


# ───────── mapping ─────────
def test_guesses_common_headers():
    m = imp.guess_mapping(["Patient Name", "Mobile Number", "Dr", "Appointment Date", "Appointment Time", "Service", "Remarks"])
    assert m == {"patient_name": "Patient Name", "phone": "Mobile Number", "doctor": "Dr", "date": "Appointment Date",
                 "time": "Appointment Time", "treatment": "Service", "language": None, "note": "Remarks"}


def test_a_column_is_never_guessed_for_two_fields():
    m = imp.guess_mapping(["Name", "Phone", "Doctor", "Date", "Time"])
    assert len({v for v in m.values() if v}) == len([v for v in m.values() if v])


# ───────── dates, times, matching ─────────
@pytest.mark.parametrize("s,d", [("2026-11-10", date(2026, 11, 10)), ("10/11/2026", date(2026, 11, 10)), ("10-11-2026", date(2026, 11, 10)),
                                 ("10.11.2026", date(2026, 11, 10)), ("10 Nov 2026", date(2026, 11, 10)), ("10 November 2026", date(2026, 11, 10)),
                                 ("Nov 10, 2026", date(2026, 11, 10)), (" 2026-11-10 ", date(2026, 11, 10))])
def test_dates(s, d):
    assert imp.parse_date(s) == d


@pytest.mark.parametrize("s", ["", "tomorrow", "31/02/2026", "2026-13-01", "10"])
def test_bad_dates(s):
    assert imp.parse_date(s) is None


@pytest.mark.parametrize("s,t", [("10:30", time(10, 30)), ("17:05", time(17, 5)), ("10:30 AM", time(10, 30)), ("5:30 pm", time(17, 30)),
                                 ("5:30PM", time(17, 30)), ("5 PM", time(17, 0)), ("10.30", time(10, 30)), ("1030", time(10, 30))])
def test_times(s, t):
    assert imp.parse_time(s) == t


@pytest.mark.parametrize("s", ["", "noon", "25:00", "10:75"])
def test_bad_times(s):
    assert imp.parse_time(s) is None


def test_doctor_matching_is_forgiving_but_never_guesses_between_two():
    assert imp.match_doctor("Dr. Arjun Mehta", DOCTORS)[0]["id"] == 2
    assert imp.match_doctor("arjun", DOCTORS)[0]["id"] == 2
    assert imp.match_doctor("Dr Mehta", DOCTORS)[0]["id"] == 2
    assert imp.match_doctor("Neha Kulkarni", DOCTORS)[0]["id"] == 1
    d, err = imp.match_doctor("Neha", DOCTORS)                                    # two Nehas: refuse to guess
    assert d is None and "more than one" in err
    d, err = imp.match_doctor("Dr. Nobody", DOCTORS)
    assert d is None and "No doctor matches" in err
    assert imp.match_doctor("", DOCTORS)[0] is None


def test_treatment_matching_and_default():
    assert imp.match_treatment("", TTS)[0]["name"] == "Consultation"             # blank -> the usual first visit
    assert imp.match_treatment("scaling", TTS)[0]["id"] == 2
    assert imp.match_treatment("Teleport", TTS)[0] is None


# ───────── Google Sheet fetch: only Google, only https ─────────
@pytest.mark.parametrize("url", ["http://docs.google.com/spreadsheets/d/e/abc/pub?output=csv", "https://evil.example/spreadsheets/d/abc",
                                 "https://docs.google.com.evil.example/spreadsheets/d/abc", "https://127.0.0.1/spreadsheets/d/abc",
                                 "https://169.254.169.254/latest/meta-data", "https://docs.google.com:8443/spreadsheets/d/abc",
                                 "https://user:pw@docs.google.com/spreadsheets/d/abc", "ftp://docs.google.com/spreadsheets/d/abc",
                                 "https://docs.google.com/document/d/abc", "file:///etc/passwd", "not a url", ""])
def test_non_google_links_are_refused_before_any_request(url):
    with pytest.raises(imp.ImportError_) as e:
        imp.normalise_sheet_url(url)
    assert e.value.code == "bad_sheet_url"


def test_share_links_are_turned_into_csv_export_links():
    assert imp.normalise_sheet_url("https://docs.google.com/spreadsheets/d/AbC_123-x/edit#gid=0") == \
        "https://docs.google.com/spreadsheets/d/AbC_123-x/export?format=csv&gid=0"
    assert imp.normalise_sheet_url("https://docs.google.com/spreadsheets/d/AbC_123-x/edit?gid=77") .endswith("&gid=77")
    pub = "https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?output=csv"
    assert imp.normalise_sheet_url(pub) == pub                                    # published links pass through


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


PUB = "https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?output=csv"


async def test_fetches_a_published_csv():
    seen = []

    async def h(req):
        seen.append(str(req.url))
        return httpx.Response(200, headers={"content-type": "text/csv; charset=utf-8"}, content="﻿Name,Phone\nAsha,98765 43210\n".encode())
    text = await imp.fetch_sheet_csv(PUB, client_for(h))
    assert text.startswith("Name,Phone") and seen == [PUB]


async def test_follows_google_redirects_but_never_to_another_host():
    async def good(req):
        if req.url.host == "docs.google.com":
            return httpx.Response(307, headers={"location": "https://doc-0c-1o-sheets.googleusercontent.com/pub/x"})
        return httpx.Response(200, headers={"content-type": "text/csv"}, content=b"a,b\n1,2\n")
    assert (await imp.fetch_sheet_csv(PUB, client_for(good))).startswith("a,b")

    hits = []

    async def evil(req):
        hits.append(req.url.host)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    with pytest.raises(imp.ImportError_) as e:
        await imp.fetch_sheet_csv(PUB, client_for(evil))
    assert e.value.code == "bad_sheet_url" and hits == ["docs.google.com"]        # the metadata address was never requested

    async def loop(req):
        return httpx.Response(302, headers={"location": PUB})
    with pytest.raises(imp.ImportError_) as e:
        await imp.fetch_sheet_csv(PUB, client_for(loop))
    assert "redirects" in e.value.message


@pytest.mark.parametrize("resp,code", [
    (httpx.Response(404), "sheet_unavailable"),
    (httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>sign in</html>"), "not_csv"),
    (httpx.Response(200, headers={"content-type": "text/csv"}, content=b"x" * (imp.MAX_BYTES + 5)), "too_large"),
])
async def test_unusable_responses_are_explained(resp, code):
    async def h(req):
        return resp
    with pytest.raises(imp.ImportError_) as e:
        await imp.fetch_sheet_csv(PUB, client_for(h))
    assert e.value.code == code


async def test_network_failure_is_reported_not_raised():
    async def h(req):
        raise httpx.ConnectError("boom")
    with pytest.raises(imp.ImportError_) as e:
        await imp.fetch_sheet_csv(PUB, client_for(h))
    assert e.value.code == "sheet_unreachable" and e.value.status == 502
