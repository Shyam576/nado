"""
modules/calendar.py — Today's macOS Calendar.app events, via AppleScript.

Read-only, no calendar API keys or OAuth needed — Calendar.app already has
whatever accounts (iCloud, Google, etc.) the user has configured. Same
"talk to the app that's already there via osascript" pattern as
actions.show_notification / modules.system.lock_screen.
"""

import datetime
import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15

# Emits one "HH:MM||summary" line per event happening today, across every
# calendar. HH:MM (24-hour, zero-padded) is both the sort key and the
# display value — simpler and locale-independent, unlike AppleScript's
# "date as string" coercion (which also throws on some all-day/recurring
# events; see the per-event try block below).
_TODAY_EVENTS_SCRIPT = """
tell application "Calendar"
    set startOfDay to current date
    set hours of startOfDay to 0
    set minutes of startOfDay to 0
    set seconds of startOfDay to 0
    set endOfDay to startOfDay + 1 * days
    set output to ""
    repeat with cal in calendars
        try
            set theseEvents to (every event of cal whose start date ≥ startOfDay and start date < endOfDay)
        on error
            set theseEvents to {}
        end try
        repeat with evt in theseEvents
            try
                set evtStart to start date of evt
                set hh to text -2 thru -1 of ("0" & (hours of evtStart))
                set mi to text -2 thru -1 of ("0" & (minutes of evtStart))
                set output to output & hh & ":" & mi & "||" & (summary of evt) & linefeed
            end try
        end repeat
    end repeat
    return output
end tell
"""


def _run_applescript(script: str) -> str:
    """Run an AppleScript via osascript and return its stdout, raising on failure."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout


def _format_time(hhmm: str) -> str:
    """Format a 24-hour 'HH:MM' string as e.g. '2:30 PM'."""
    return datetime.datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")


def get_today_events() -> Optional[list[dict]]:
    """Return today's Calendar.app events, sorted by start time — structured.

    The dashboard's Today-page context panel and today_events() (the bot's
    text-formatting wrapper) both call this; the AppleScript run + parse
    logic lives here exactly once.

    Returns:
        A list of {"time": "HH:MM", "summary": str} dicts (empty list if
        there are none today), or None if the calendar couldn't be read at
        all (wrong platform, AppleScript/permission failure) — distinct from
        "read fine, nothing on it."
    """
    if sys.platform != "darwin":
        return None

    try:
        raw = _run_applescript(_TODAY_EVENTS_SCRIPT)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Calendar AppleScript failed: %s", exc)
        return None

    events: list[dict] = []
    for line in raw.strip().splitlines():
        if "||" not in line:
            continue
        hhmm, summary = line.split("||", 1)
        events.append({"time": hhmm.strip(), "summary": summary.strip() or "(untitled event)"})

    events.sort(key=lambda e: e["time"])
    return events


def today_events(chat_id: str = "", args: list[str] | None = None) -> str:
    """Return today's Calendar.app events, sorted by start time, as chat text.

    Args:
        chat_id: Unused — the calendar is machine-global.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A newline-separated list of today's events, a "no events" message,
        or an error string if Calendar.app couldn't be reached (e.g.
        missing Automation permission the first time this runs).
    """
    events = get_today_events()
    if events is None:
        if sys.platform != "darwin":
            return "Calendar is only wired up for macOS right now."
        return (
            "Couldn't read Calendar.app — if this is the first time, macOS may need "
            "Automation permission granted under System Settings > Privacy & Security > Automation."
        )

    if not events:
        return "No events on your calendar today."

    lines = ["Today's events:"]
    for event in events:
        lines.append(f"  {_format_time(event['time'])} — {event['summary']}")
    return "\n".join(lines)
