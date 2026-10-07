"""
modules/life_clock.py — "life passing by" counter for the dashboard landing page.

No chat_id anywhere here — DATE_OF_BIRTH is a single, config-wide fact about
the one owner of this bot (config.py), not per-chat data, same reasoning as
OWNER_ID itself. All the actual ticking math (years/months/days/hours live,
the progress bar, the remaining-time estimate) happens client-side in
dashboard/static/life-clock.js — this module only hands over the two static
inputs it needs: date of birth, and the user-editable assumed-lifespan
setting used for the countdown estimate.

assumed_lifespan_years is explicitly framed everywhere (here, in the API
response, and in the page itself) as a user-editable assumption, never a
claimed fact — there's no way to know someone's actual lifespan in advance,
so this never guesses or defaults to a "researched" figure presented as
authoritative. It's a round, clearly-adjustable starting point (80) that the
user can change to whatever they want.
"""

import memory

_DEFAULT_ASSUMED_LIFESPAN_YEARS = 80.0
_LIFESPAN_PREFERENCE_KEY = "life_clock_assumed_lifespan_years"


def get_config() -> dict:
    """Return the current life-clock settings.

    Returns:
        {"date_of_birth": "YYYY-MM-DD", "assumed_lifespan_years": float}
    """
    from config import DATE_OF_BIRTH  # local import — keeps config.py's reload-ability in tests

    return {
        "date_of_birth": DATE_OF_BIRTH,
        "assumed_lifespan_years": float(
            memory.get_preference(_LIFESPAN_PREFERENCE_KEY, _DEFAULT_ASSUMED_LIFESPAN_YEARS)
        ),
    }


def set_assumed_lifespan(years: float) -> dict:
    """Update the assumed-lifespan setting used for the countdown estimate.

    Args:
        years: The new assumed total lifespan in years. Must be positive.

    Returns:
        The updated config, same shape as get_config().

    Raises:
        ValueError: If `years` is not positive.
    """
    if years <= 0:
        raise ValueError("Assumed lifespan must be positive")
    memory.set_preference(_LIFESPAN_PREFERENCE_KEY, years)
    return get_config()
