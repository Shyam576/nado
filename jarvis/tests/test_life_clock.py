"""tests/test_life_clock.py — the life-clock landing page's one piece of
server-side state: the user-editable assumed-lifespan preference. The
ticking age/remaining-time math itself lives client-side in
dashboard/static/life-clock.js (nothing to unit test server-side there)."""

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
