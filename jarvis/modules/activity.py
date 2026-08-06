"""
modules/activity.py — ambient activity tracking: which app + window is frontmost.

Read-only, local-only (nothing leaves the machine) — periodically samples
the frontmost application and window title via AppleScript/System Events,
same "talk to the OS via osascript" pattern as modules/calendar_app.py and
actions.show_notification. Feeds a daily "how did today break down" summary
and an occasional "you've been in the same window for hours, need a break?"
check-in — the point is a felt sense that Jarvis notices things without
being asked, not a surveillance log.

Requires macOS Accessibility permission granted to whatever process runs
this — a one-time manual step (System Settings > Privacy & Security >
Accessibility). The "System Events" tell-block used for window titles needs
a stricter permission than Calendar.app's Automation permission; confirmed
by testing live against this machine, where it hung waiting on a consent
dialog that can't be answered headlessly. Every function here fails
gracefully with a message pointing at that setting rather than raising.
"""

import datetime
import logging
import subprocess
import sys
import time
from collections import defaultdict
from typing import Optional

import memory
from store.db import get_connection

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10

# Must match the polling interval the background job actually samples at —
# each stored row is treated as representing this many minutes of that app
# being frontmost. An approximation (not continuous tracking), accurate
# enough for a "how did today break down" summary.
SAMPLE_INTERVAL_MINUTES = 5

# How many consecutive identical (app, window) samples count as a "long
# stretch" worth a check-in nudge. 24 samples * 5 min = ~2 hours.
_LONG_STRETCH_SAMPLES = 24
_LONG_STRETCH_ALERT_COOLDOWN_SECONDS = 3 * 3600

_FRONTMOST_SCRIPT = """
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
end tell
set winTitle to ""
try
    tell application "System Events"
        tell process frontApp
            set winTitle to name of front window
        end tell
    end tell
end try
return frontApp & "||" & winTitle
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
    return result.stdout.strip()


def sample_frontmost(chat_id: str) -> None:
    """Capture the current frontmost app + window title and store one row.

    Intended to be polled periodically by a background job. Silently no-ops
    on any failure (missing Accessibility permission, no GUI session, a
    screen locked long enough that System Events can't answer, etc.) — this
    is best-effort ambient data, not a critical path, so a failure here
    should never surface as a user-facing error.

    Args:
        chat_id: The owner this sample belongs to.
    """
    if sys.platform != "darwin":
        return

    try:
        raw = _run_applescript(_FRONTMOST_SCRIPT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Activity sample failed (Accessibility permission granted?): %s", exc)
        return

    if "||" not in raw:
        return
    app_name, window_title = (part.strip() for part in raw.split("||", 1))
    if not app_name:
        return

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO activity_log (chat_id, app_name, window_title, captured_at) VALUES (?, ?, ?, ?)",
            (chat_id, app_name, window_title or None, datetime.datetime.now().isoformat()),
        )


def today_summary(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Summarise today's frontmost-app time, aggregated by app.

    Args:
        chat_id: The owner to summarise activity for.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A per-app time breakdown (with each app's most-sampled window as a
        representative detail), sorted by time descending, or a message if
        nothing has been sampled yet today.
    """
    today = datetime.date.today().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT app_name, window_title, COUNT(*) AS samples FROM activity_log "
            "WHERE chat_id = ? AND date(captured_at) = ? "
            "GROUP BY app_name, window_title ORDER BY samples DESC",
            (chat_id, today),
        ).fetchall()

    if not rows:
        return "No activity data for today yet."

    app_minutes: dict[str, int] = defaultdict(int)
    app_top_window: dict[str, tuple[str, int]] = {}
    for row in rows:
        minutes = row["samples"] * SAMPLE_INTERVAL_MINUTES
        app_minutes[row["app_name"]] += minutes
        if row["window_title"]:
            current = app_top_window.get(row["app_name"])
            if current is None or row["samples"] > current[1]:
                app_top_window[row["app_name"]] = (row["window_title"], row["samples"])

    lines = ["Today's activity:"]
    for app, minutes in sorted(app_minutes.items(), key=lambda kv: -kv[1]):
        hours, mins = divmod(minutes, 60)
        duration = f"{hours}h {mins}m" if hours else f"{mins}m"
        detail = f" ({app_top_window[app][0]})" if app in app_top_window else ""
        lines.append(f"  {app}: {duration}{detail}")
    return "\n".join(lines)


def check_long_stretch(chat_id: str) -> Optional[str]:
    """Return a check-in nudge if the same app/window has been frontmost for a long stretch.

    Fires at most once per _LONG_STRETCH_ALERT_COOLDOWN_SECONDS for the same
    (app, window) pair, so it nudges rather than nags.

    Args:
        chat_id: The owner to check.

    Returns:
        A nudge message, or None if no long stretch is detected or one was
        already sent recently for it.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT app_name, window_title FROM activity_log "
            "WHERE chat_id = ? ORDER BY captured_at DESC LIMIT ?",
            (chat_id, _LONG_STRETCH_SAMPLES),
        ).fetchall()

    if len(rows) < _LONG_STRETCH_SAMPLES:
        return None

    first_app, first_window = rows[0]["app_name"], rows[0]["window_title"]
    if any(row["app_name"] != first_app or row["window_title"] != first_window for row in rows):
        return None

    state_key = f"activity_long_stretch_alerted_{chat_id}_{first_app}_{first_window}"
    last_alerted = memory.get_preference(state_key)
    now = time.time()
    if last_alerted and now - float(last_alerted) < _LONG_STRETCH_ALERT_COOLDOWN_SECONDS:
        return None
    memory.set_preference(state_key, now)

    hours = (_LONG_STRETCH_SAMPLES * SAMPLE_INTERVAL_MINUTES) / 60
    detail = f" ({first_window})" if first_window else ""
    return f"You've been in {first_app}{detail} for {hours:.0f}+ hours straight — need a break, or is this going well?"
