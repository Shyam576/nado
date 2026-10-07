"""
modules/life_clock.py — "life passing by" counter for the dashboard landing
page, plus the once-daily morning chat notification (bot/telegram_bot.py).

No chat_id anywhere here — DATE_OF_BIRTH is a single, config-wide fact about
the one owner of this bot (config.py), not per-chat data, same reasoning as
OWNER_ID itself. The dashboard's live, ticking-to-the-second display (years/
months/days/hours/minutes/seconds, the progress ring) happens client-side in
dashboard/static/life-clock.js — seconds-level precision has nothing to add
to a once-a-day text message, so describe_countdown() below is a separate,
coarser (years/months/days only) server-side port of the same calendar-
correct breakdown logic, for exactly that one daily snapshot.

assumed_lifespan_years is explicitly framed everywhere (here, in the API
response, and in the page itself) as a user-editable assumption, never a
claimed fact — there's no way to know someone's actual lifespan in advance,
so this never guesses or defaults to a "researched" figure presented as
authoritative. It's a round, clearly-adjustable starting point (80) that the
user can change to whatever they want.
"""

import datetime

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


# ---------------------------------------------------------------------------
# Daily notification text — server-side port of life-clock.js's calendar-
# correct breakdown (years/months/days only; see module docstring for why
# hours/minutes/seconds don't belong in a once-a-day message).
# ---------------------------------------------------------------------------


def _diff_breakdown(earlier: datetime.date, later: datetime.date) -> dict:
    """Calendar-correct years/months/days between two dates (earlier <= later)."""
    years = later.year - earlier.year
    months = later.month - earlier.month
    days = later.day - earlier.day

    if days < 0:
        prev_month_last_day = (later.replace(day=1) - datetime.timedelta(days=1)).day
        days += prev_month_last_day
        months -= 1
    if months < 0:
        months += 12
        years -= 1

    return {"years": years, "months": months, "days": days}


def _add_years(d: datetime.date, years: float) -> datetime.date:
    """Add a (possibly fractional) number of years to a date."""
    whole_years = int(years)
    try:
        new_date = d.replace(year=d.year + whole_years)
    except ValueError:
        # d was Feb 29 and d.year + whole_years isn't a leap year
        new_date = d.replace(year=d.year + whole_years, day=28)
    fractional_days = round((years - whole_years) * 365.25)
    return new_date + datetime.timedelta(days=fractional_days)


def _format_breakdown(b: dict) -> str:
    parts = []
    if b["years"]:
        parts.append(f"{b['years']} year{'s' if b['years'] != 1 else ''}")
    if b["months"] or b["years"]:
        parts.append(f"{b['months']} month{'s' if b['months'] != 1 else ''}")
    parts.append(f"{b['days']} day{'s' if b['days'] != 1 else ''}")
    return ", ".join(parts)


def describe_countdown() -> str:
    """Render today's life-clock snapshot as chat text (years/months/days only).

    Used by the once-daily morning notification (bot/telegram_bot.py) — see
    the module docstring for why this doesn't need the dashboard's live
    hours/minutes/seconds precision.

    Returns:
        A short multi-line message: time lived, and the remaining-time
        estimate against the current assumed lifespan (or a note if that
        assumed lifespan has already passed).
    """
    config = get_config()
    dob = datetime.date.fromisoformat(config["date_of_birth"])
    today = datetime.date.today()
    lifespan_years = config["assumed_lifespan_years"]

    lived = _diff_breakdown(dob, today)
    target = _add_years(dob, lifespan_years)

    lines = [f"You've lived {_format_breakdown(lived)}."]
    if target > today:
        remaining = _diff_breakdown(today, target)
        lines.append(
            f"Estimated time remaining: ~{_format_breakdown(remaining)} "
            f"(assumed lifespan: {lifespan_years:.0f} years)."
        )
    else:
        lines.append(f"You've passed the {lifespan_years:.0f}-year assumed lifespan you set.")
    return "\n".join(lines)
