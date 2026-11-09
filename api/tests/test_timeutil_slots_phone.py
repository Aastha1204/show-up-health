"""Pure tests: timezone handling, slot generation, phone normalisation. No database needed."""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.phone import normalize_phone
from app.services.slots import compute_slots, is_slot_valid, overlaps
from app.timeutil import (IST, day_before_due, ist_day_bounds, ist_to_utc, to_ist, window_of)

HOURS = [["10:00", "13:30"], ["17:00", "21:00"]]
WH = {k: (HOURS if k != "sun" else []) for k in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}
MON = date(2030, 1, 7)   # a Monday
SUN = date(2030, 1, 6)
LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)


def at(d, hhmm):
    h, m = hhmm.split(":")
    return ist_to_utc(d, time(int(h), int(m)))


# ───────── timezone ─────────
def test_1030_ist_is_0500_utc_and_day_before_due_is_previous_day_0500_utc():
    start = at(MON, "10:30")
    assert start == datetime(2030, 1, 7, 5, 0, tzinfo=timezone.utc)
    assert day_before_due(start) == datetime(2030, 1, 6, 5, 0, tzinfo=timezone.utc)
    assert to_ist(day_before_due(start)).strftime("%a %H:%M") == "Sun 10:30"


def test_ist_day_bounds_are_1830_utc_to_1830_utc():
    lo, hi = ist_day_bounds(MON)
    assert lo == datetime(2030, 1, 6, 18, 30, tzinfo=timezone.utc)
    assert hi == datetime(2030, 1, 7, 18, 30, tzinfo=timezone.utc)


def test_just_after_ist_midnight_belongs_to_the_ist_day_not_the_utc_day():
    lo, hi = ist_day_bounds(MON)
    t = at(MON, "00:15")  # = 18:45 UTC on Sunday
    assert t.date() == SUN and lo <= t < hi


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        to_ist(datetime(2030, 1, 7, 10, 30))


@pytest.mark.parametrize("t,w", [("10:00", "morning"), ("13:45", "morning"), ("14:00", "afternoon"),
                                 ("16:59", "afternoon"), ("17:00", "evening"), ("20:45", "evening")])
def test_waitlist_window(t, w):
    h, m = t.split(":")
    assert window_of(time(int(h), int(m))) == w


# ───────── slots ─────────
def test_grid_and_range_end_for_20_minute_visits():
    s = compute_slots(WH, MON, 20, [], LONG_AGO)
    local = [x.astimezone(IST).strftime("%H:%M") for x in s]
    # 13:00 + 20 min = 13:20 fits; 13:15 + 20 min = 13:35 would run past closing
    assert local[0] == "10:00" and "13:00" in local and "13:15" not in local and "13:30" not in local
    assert "17:00" in local and local[-1] == "20:30"
    assert all(int(x[-2:]) % 15 == 0 for x in local)


def test_long_visit_must_fit_inside_one_range():
    local = [x.astimezone(IST).strftime("%H:%M") for x in compute_slots(WH, MON, 60, [], LONG_AGO)]
    assert "12:30" in local and "12:45" not in local      # 12:45 + 60 crosses 13:30
    assert "13:00" not in local and "16:00" not in local  # nothing straddles the lunch gap


def test_closed_on_sunday():
    assert compute_slots(WH, SUN, 20, [], LONG_AGO) == []


def test_variable_duration_overlap_blocks_the_right_slots():
    # a 60-minute appointment at 10:00 blocks a 20-minute slot at 10:30 (the case a unique index would miss)
    busy = [(at(MON, "10:00"), at(MON, "11:00"))]
    local = [x.astimezone(IST).strftime("%H:%M") for x in compute_slots(WH, MON, 20, busy, LONG_AGO)]
    assert not {"10:00", "10:15", "10:30", "10:45"} & set(local)
    assert "11:00" in local  # touching boundary is fine: half-open intervals


def test_slot_before_busy_must_not_run_into_it():
    busy = [(at(MON, "11:00"), at(MON, "11:20"))]
    local = [x.astimezone(IST).strftime("%H:%M") for x in compute_slots(WH, MON, 30, busy, LONG_AGO)]
    assert "10:30" in local and "10:45" not in local and "11:15" not in local and "11:30" in local


def test_past_slots_are_excluded():
    now = at(MON, "10:40")
    local = [x.astimezone(IST).strftime("%H:%M") for x in compute_slots(WH, MON, 20, [], now)]
    assert local[0] == "10:45"
    assert compute_slots(WH, MON, 20, [], at(MON, "21:00")) == []


def test_min_lead_time():
    now = at(MON, "10:00")
    local = [x.astimezone(IST).strftime("%H:%M") for x in
             compute_slots(WH, MON, 20, [], now, min_lead=timedelta(hours=2))]
    assert local[0] == "12:00"


def test_overlap_is_half_open():
    a, b, c = at(MON, "10:00"), at(MON, "11:00"), at(MON, "12:00")
    assert not overlaps(a, b, b, c) and overlaps(a, c, b, c)


@pytest.mark.parametrize("hhmm,dur,ok", [
    ("10:00", 20, True), ("13:00", 20, True), ("13:15", 20, False), ("13:30", 20, False),
    ("13:15", 15, True), ("13:10", 20, False),  # 13:10 is off the grid
    ("13:20", 20, False),   # off the 15-minute grid
    ("13:00", 45, False),   # runs past 13:30
    ("12:45", 45, True), ("09:45", 20, False), ("20:45", 15, True), ("20:50", 15, False),
])
def test_is_slot_valid(hhmm, dur, ok):
    assert is_slot_valid(WH, at(MON, hhmm), dur) is ok


def test_slot_validity_is_evaluated_in_ist_not_utc():
    # 10:00 IST is 04:30 UTC: a UTC-based check would think it is 04:30 and reject it
    assert is_slot_valid(WH, datetime(2030, 1, 7, 4, 30, tzinfo=timezone.utc), 20)
    assert not is_slot_valid(WH, datetime(2030, 1, 7, 10, 0, tzinfo=timezone.utc), 20)  # = 15:30 IST, closed


# ───────── phone ─────────
@pytest.mark.parametrize("raw,out", [
    ("98765 43210", "+919876543210"), ("+91 98765-43210", "+919876543210"), ("09876543210", "+919876543210"),
    ("919876543210", "+919876543210"), ("0091 9876543210", "+919876543210"), ("+14155550123", "+14155550123"),
])
def test_phone_normalised(raw, out):
    assert normalize_phone(raw) == out


@pytest.mark.parametrize("raw", ["", "12345", "+91 12345 67890", "abcdefghij", "+0123456789", "98765432101234567"])
def test_phone_rejected(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)
