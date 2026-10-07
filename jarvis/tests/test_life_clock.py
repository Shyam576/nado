"""tests/test_life_clock.py — the life-clock landing page's server-side
state (the user-editable assumed-lifespan preference) and the once-daily
notification text (describe_countdown and its calendar-correct breakdown
helpers). The dashboard's live, ticking-to-the-second math lives client-side
in dashboard/static/life-clock.js — nothing to unit test server-side there."""

import datetime

import memory
from modules import life_clock


def test_get_config_returns_date_of_birth_and_default_lifespan():
    config = life_clock.get_config()
    assert config["date_of_birth"] == "2001-09-06"
    assert config["assumed_lifespan_years"] == 80.0


def test_set_assumed_lifespan_updates_and_persists():
    life_clock.set_assumed_lifespan(90)
    assert memory.get_preference("life_clock_assumed_lifespan_years") == 90

    config = life_clock.get_config()
    assert config["assumed_lifespan_years"] == 90.0


def test_set_assumed_lifespan_rejects_zero_or_negative():
    for bad in (0, -5):
        try:
            life_clock.set_assumed_lifespan(bad)
            assert False, f"expected ValueError for {bad}"
        except ValueError:
            pass

    # Rejected value must not have been saved
    assert life_clock.get_config()["assumed_lifespan_years"] == 80.0


# ---------------------------------------------------------------------------
# Calendar-correct breakdown (_diff_breakdown / _add_years) — fixed dates
# only, so these don't depend on "today" and stay deterministic forever.
# ---------------------------------------------------------------------------


def test_diff_breakdown_simple_case():
    b = life_clock._diff_breakdown(datetime.date(2001, 9, 6), datetime.date(2026, 10, 7))
    assert b == {"years": 25, "months": 1, "days": 1}


def test_diff_breakdown_borrows_days_from_previous_month():
    # 2024-01-15 -> 2024-03-01: day underflows (1 - 15), borrows from Feb
    # (a leap year, 29 days): 1 month, 15 days. Hand-verified: 2024-01-15 +
    # 1 month = 2024-02-15, + 15 days = 2024-03-01.
    b = life_clock._diff_breakdown(datetime.date(2024, 1, 15), datetime.date(2024, 3, 1))
    assert b == {"years": 0, "months": 1, "days": 15}


def test_diff_breakdown_zero_for_identical_dates():
    d = datetime.date(2020, 5, 5)
    assert life_clock._diff_breakdown(d, d) == {"years": 0, "months": 0, "days": 0}


def test_add_years_handles_leap_day_target_non_leap_year():
    # 2020 is a leap year, 2021 isn't — Feb 29 has no equivalent, falls back to Feb 28.
    result = life_clock._add_years(datetime.date(2020, 2, 29), 1)
    assert result == datetime.date(2021, 2, 28)


def test_add_years_whole_years():
    result = life_clock._add_years(datetime.date(2001, 9, 6), 25)
    assert result == datetime.date(2026, 9, 6)


def test_add_years_fractional_lands_roughly_mid_year():
    result = life_clock._add_years(datetime.date(2020, 1, 1), 1.5)
    assert datetime.date(2021, 6, 1) <= result <= datetime.date(2021, 7, 15)


# ---------------------------------------------------------------------------
# describe_countdown — structure only (not exact numbers, which depend on
# "today" and would make this test fragile).
# ---------------------------------------------------------------------------


def test_describe_countdown_reports_lived_and_remaining():
    text = life_clock.describe_countdown()
    assert "You've lived" in text
    assert "Estimated time remaining" in text
    assert "assumed lifespan: 80 years" in text


def test_describe_countdown_handles_an_already_passed_assumed_lifespan():
    life_clock.set_assumed_lifespan(1)  # DOB is 2001 — 1 year is long past by now
    text = life_clock.describe_countdown()
    assert "passed the 1-year assumed lifespan" in text
