"""The seed's own invariants (no database): bookable times, determinism, headline numbers."""
from datetime import datetime, timezone

import pytest

from app import seed
from app.services.slots import is_slot_valid
from app.timeutil import IST

NOWS = [datetime(2026, 11, 8, 3, 25, tzinfo=timezone.utc), datetime(2026, 11, 9, 5, 10, tzinfo=timezone.utc),
        datetime(2026, 11, 10, 4, 30, tzinfo=timezone.utc), datetime(2026, 11, 10, 16, 45, tzinfo=timezone.utc),
        datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc)]


@pytest.mark.parametrize("now", NOWS)
def test_every_seeded_appointment_is_on_the_bookable_grid_and_inside_working_hours(now):
    ds = seed.generate(now)                                   # check() runs inside, with the same assertion
    for a in ds["appointments"]:
        local = a["start_at"].astimezone(IST)
        assert local.minute % 15 == 0 and local.second == 0, local
        assert is_slot_valid(seed.WORKING_HOURS, a["start_at"], a["duration_min"]), a["start_at"]


def test_the_cancel_me_slot_can_always_be_offered_to_the_waitlist():
    """If it were off-grid the engine would refuse to offer it and the live demo's step 4 would silently do nothing."""
    for now in NOWS:
        ds = seed.generate(now)
        cm = next(a for a in ds["appointments"] if a["id"] == ds["cancel_me_id"])
        assert is_slot_valid(seed.WORKING_HOURS, cm["start_at"], cm["duration_min"])


def test_seed_is_deterministic_for_a_given_moment():
    a, b = seed.generate(NOWS[1]), seed.generate(NOWS[1])
    assert [x["id"] for x in a["appointments"]] == [x["id"] for x in b["appointments"]]
    assert [x["start_at"] for x in a["appointments"]] == [x["start_at"] for x in b["appointments"]]
