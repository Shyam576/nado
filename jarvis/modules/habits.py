"""
modules/habits.py — Mood journaling and habit streak tracking.

Mood entries are freeform, timestamped log rows (mood_log). Habits are
logged once per day per habit name (habit_log); "streak" means consecutive
calendar days with a log entry, ending today or yesterday (a still-open
streak if today's log hasn't happened yet).
"""

import datetime
import logging

import memory
from store.db import get_connection

logger = logging.getLogger(__name__)

_HABIT_GAP_ALERT_DAYS = 3


def log_mood(chat_id: str = "", args: list[str] | None = None) -> str:
    """Log a mood entry, with optional energy (1-10) and freeform note.

    Args:
        chat_id: The chat logging this mood entry.
        args: [mood, (energy 1-10)?, note words...]. Only `mood` is required.

    Returns:
        A confirmation, or a usage message if no mood word was given.
    """
    args = args or []
    if not args:
        return "Usage: /mood <mood> [1-10] [note]"

    mood = args[0]
    rest = args[1:]

    energy = None
    if rest and rest[0].isdigit() and 1 <= int(rest[0]) <= 10:
        energy = int(rest[0])
        rest = rest[1:]
    note = " ".join(rest).strip() or None

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO mood_log (chat_id, mood, energy, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, mood, energy, note, datetime.datetime.now().isoformat()),
        )

    confirmation = f"Logged mood: {mood}"
    if energy is not None:
        confirmation += f" (energy {energy}/10)"
    return confirmation


def mood_history(chat_id: str = "", args: list[str] | None = None) -> str:
    """Return the last 5 mood entries for this chat.

    Args:
        chat_id: The chat to look up.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A newline-separated list of recent mood entries, or a message if none exist.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT mood, energy, note, created_at FROM mood_log "
            "WHERE chat_id = ? ORDER BY created_at DESC LIMIT 5",
            (chat_id,),
        ).fetchall()

    if not rows:
        return "No mood entries logged yet."

    lines = []
    for row in rows:
        when = datetime.datetime.fromisoformat(row["created_at"]).strftime("%b %-d, %-I:%M %p")
        line = f"{when} — {row['mood']}"
        if row["energy"] is not None:
            line += f" (energy {row['energy']}/10)"
        if row["note"]:
            line += f": {row['note']}"
        lines.append(line)
    return "\n".join(lines)


def weekly_mood_entries(chat_id: str) -> list[dict]:
    """Return mood entries from the last 7 days, oldest first.

    Args:
        chat_id: The chat to look up.

    Returns:
        A list of dicts with keys: mood, energy, note, created_at.
    """
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT mood, energy, note, created_at FROM mood_log "
            "WHERE chat_id = ? AND created_at >= ? ORDER BY created_at",
            (chat_id, week_ago),
        ).fetchall()
    return [dict(row) for row in rows]


def log_habit(chat_id: str = "", args: list[str] | None = None) -> str:
    """Log today's completion of a habit (idempotent — one log per day).

    Args:
        chat_id: The chat logging this habit.
        args: [habit_name].

    Returns:
        A confirmation with the current streak, or a usage message.
    """
    args = args or []
    if not args:
        return "Usage: /habit <name>"

    habit = args[0].lower()
    today = datetime.date.today().isoformat()

    with get_connection() as conn:
        already_logged = conn.execute(
            "SELECT 1 FROM habit_log WHERE chat_id = ? AND habit = ? AND date(completed_at) = ?",
            (chat_id, habit, today),
        ).fetchone()
        if not already_logged:
            conn.execute(
                "INSERT INTO habit_log (chat_id, habit, completed_at) VALUES (?, ?, ?)",
                (chat_id, habit, datetime.datetime.now().isoformat()),
            )

    streak = _current_streak(chat_id, habit)
    return f"Logged '{habit}' for today. Current streak: {streak} day{'s' if streak != 1 else ''}."


def habit_status(chat_id: str = "", args: list[str] | None = None) -> str:
    """Show the streak for one habit, or all tracked habits for this chat.

    Args:
        chat_id: The chat to look up.
        args: Optional [habit_name] to scope to one habit.

    Returns:
        Streak summary text.
    """
    args = args or []
    with get_connection() as conn:
        if args:
            habit = args[0].lower()
            streak = _current_streak(chat_id, habit)
            return f"'{habit}': {streak} day{'s' if streak != 1 else ''} streak."

        habits = [
            row["habit"]
            for row in conn.execute(
                "SELECT DISTINCT habit FROM habit_log WHERE chat_id = ? ORDER BY habit", (chat_id,)
            ).fetchall()
        ]

    if not habits:
        return "No habits tracked yet. Log one with /habit <name>."

    lines = [f"'{h}': {_current_streak(chat_id, h)} day streak" for h in habits]
    return "\n".join(lines)


def _completed_dates(chat_id: str, habit: str) -> set[datetime.date]:
    """Return the distinct calendar dates a habit was logged for this chat."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date(completed_at) AS d FROM habit_log WHERE chat_id = ? AND habit = ?",
            (chat_id, habit),
        ).fetchall()
    return {datetime.date.fromisoformat(row["d"]) for row in rows}


def _current_streak(chat_id: str, habit: str) -> int:
    """Count consecutive days logged, ending today or yesterday.

    A streak counted as "still alive" if today or yesterday has a log entry
    (today may not be logged yet without breaking the streak); otherwise 0.
    """
    dates = _completed_dates(chat_id, habit)
    if not dates:
        return 0

    today = datetime.date.today()
    cursor = today if today in dates else today - datetime.timedelta(days=1)
    if cursor not in dates:
        return 0

    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= datetime.timedelta(days=1)
    return streak


def check_habit_gaps() -> list[tuple[str, str]]:
    """Check every tracked (chat, habit) pair for a gap of _HABIT_GAP_ALERT_DAYS+.

    Fires at most once per calendar day per habit (tracked via a memory
    preference), regardless of how long the gap grows after that.

    Returns:
        A list of (chat_id, alert_message) tuples for any habit(s) newly
        past the gap threshold today — unlike gold/TER targets, habits are
        already scoped per chat_id, so alerts target that chat specifically
        rather than broadcasting to every allowlisted chat.
    """
    with get_connection() as conn:
        pairs = conn.execute("SELECT DISTINCT chat_id, habit FROM habit_log").fetchall()

    today = datetime.date.today()
    alerts: list[tuple[str, str]] = []

    for row in pairs:
        chat_id, habit = row["chat_id"], row["habit"]
        dates = _completed_dates(chat_id, habit)
        if not dates:
            continue

        last_completed = max(dates)
        gap_days = (today - last_completed).days
        if gap_days < _HABIT_GAP_ALERT_DAYS:
            continue

        alert_key = f"habit_gap_alerted_{chat_id}_{habit}"
        if memory.get_preference(alert_key) == today.isoformat():
            continue  # already alerted today

        memory.set_preference(alert_key, today.isoformat())
        alerts.append((chat_id, f"You've skipped '{habit}' for {gap_days} days — last done {last_completed.isoformat()}."))

    return alerts
