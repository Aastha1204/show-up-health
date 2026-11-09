"""Pure tests for patient actions: STOP detection and the reschedule list builder."""
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

from app.messaging.optout import is_stop_request
from app.services import reschedule
from app.timeutil import ist_to_utc

# ───────── STOP detection ─────────
@pytest.mark.parametrize("text", [
    "STOP", "stop", "Stop.", "STOP!!", "  stop  ", "Stop\n", "Unsubscribe", "unsubscribe me", "UNSUBSCRIBE",
    "please stop", "stop messaging me", "Stop all", "stopp", "stoop", "unsubcribe",
    "बंद", "बंद करो", "बंद करें", "बन्द", "थांबा", "रोको",
])
def test_stop_variants_opt_out(text):
    assert is_stop_request(text), text


@pytest.mark.parametrize("text", [
    "", "   ", "hello", "yes please", "can you stop by at 5 pm?", "my phone stopped working", "I will stop by tomorrow",
    "don't stop the reminders", "dont stop", "never stop", "do not stop", "shop", "step", "top", "stopwatch",
    "when does the clinic open", "मत बंद करो", "बंद नहीं", "Please do not unsubscribe me",
])
def test_ordinary_messages_do_not_opt_out(text):
    assert not is_stop_request(text), text


# ───────── reschedule list ─────────
def opts(n_days=3, per_day=3, doctor="Dr. Neha Kulkarni"):
    d0 = datetime(2030, 1, 7).date()
    did = uuid.uuid4()
    return [reschedule.SlotOption(did, doctor, ist_to_utc(d0 + timedelta(days=i), time(10 + 2 * j, 30)))
            for i in range(n_days) for j in range(per_day)]


def test_spread_picks_earliest_middle_latest():
    assert reschedule.spread(list(range(10)), 3) == [0, 4, 9]
    assert reschedule.spread([1, 2], 3) == [1, 2]
    assert reschedule.spread([], 3) == []
    assert reschedule.spread(list(range(7)), 1) == [0]          # one slot wanted: used when the list is one short
    assert reschedule.spread(list(range(7)), 0) == []


def test_list_respects_whatsapp_limits_and_groups_by_date():
    appt = uuid.uuid4()
    lst = reschedule.build_list(appt, opts())
    rows = [r for s in lst["sections"] for r in s["rows"]]
    assert lst["header"] == "Pick a new time" and lst["button"] == "See slots" and len(lst["button"]) <= 20
    assert len(rows) == 10, "9 slots + 'None of these work'"
    assert rows[-1]["id"] == f"RS_NONE:{appt}" and rows[-1]["title"] == "None of these work"
    assert all(len(r["title"]) <= 24 and len(r["description"]) <= 72 and len(r["id"]) <= 200 for r in rows)
    dated = [s for s in lst["sections"] if s["title"] != "Other"]
    assert len(dated) == 3 and all(len(s["rows"]) == 3 for s in dated)         # sections grouped by date
    assert all(len(s["title"]) <= 24 for s in lst["sections"])
    assert rows[0]["title"] == "Mon 7 Jan · 10:30 AM" and rows[0]["description"] == "Dr. Neha Kulkarni"


def test_row_id_roundtrip_and_malformed_ids():
    appt, doc = uuid.uuid4(), uuid.uuid4()
    start = ist_to_utc(datetime(2030, 1, 7).date(), time(10, 30))
    assert reschedule.parse_row_id(reschedule.row_id(appt, doc, start)) == (appt, doc, start)
    assert reschedule.row_id(appt, doc, start).endswith(f":{int(start.timestamp())}")
    for bad in ["", "RS", "RS:a:b:c", f"RS:{appt}:{doc}:notanumber", f"X:{appt}:{doc}:1", f"RS:{appt}:{doc}:1:2"]:
        assert reschedule.parse_row_id(bad) is None, bad


def test_long_doctor_name_is_truncated_to_72():
    lst = reschedule.build_list(uuid.uuid4(), opts(1, 1, doctor="Dr. " + "X" * 100))
    assert all(len(r["description"]) <= 72 for s in lst["sections"] for r in s["rows"])
