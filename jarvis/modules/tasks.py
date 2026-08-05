"""
modules/tasks.py — Task and reminder module, backed by the SQLite store.

Exposes plain functions the bot's command dispatcher calls directly —
no LLM round-trip needed for structured task/reminder operations
(see JARVIS_V2_PLAN.md §6).
"""

import datetime
import logging

from store.db import ALLOWED_TASK_TRANSITIONS, get_connection

logger = logging.getLogger(__name__)


def add_task(chat_id: str, args: list[str]) -> str:
    """Add a new pending task for this chat.

    Args:
        chat_id: The Telegram chat ID that owns this task.
        args: Words making up the task title, e.g. ["Buy", "groceries"].

    Returns:
        A confirmation string, or a usage message if no title was given.
    """
    title = " ".join(args).strip()
    if not title:
        return "Usage: /tasks add <title>"

    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (chat_id, title, status, created_at) VALUES (?, ?, 'pending', ?)",
            (chat_id, title, now),
        )
        task_id = cursor.lastrowid

    return f"Task #{task_id} added: {title}"


def list_tasks(chat_id: str) -> str:
    """List this chat's pending tasks.

    Args:
        chat_id: The Telegram chat ID to list tasks for.

    Returns:
        A newline-separated list of pending tasks, or a message if there are none.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title FROM tasks WHERE chat_id = ? AND status = 'pending' ORDER BY created_at",
            (chat_id,),
        ).fetchall()

    if not rows:
        return "No pending tasks. Add one with /tasks add <title>."

    lines = [f"#{row['id']} {row['title']}" for row in rows]
    return "Pending tasks:\n" + "\n".join(lines)


def complete_task(chat_id: str, args: list[str]) -> str:
    """Mark a task as done.

    Args:
        chat_id: The Telegram chat ID attempting the completion (ownership check).
        args: Expected to contain the task ID as the first element.

    Returns:
        A confirmation, or an error message if the ID is invalid, not found,
        not owned by this chat, or already in a terminal state.
    """
    if not args or not args[0].isdigit():
        return "Usage: /tasks done <id>"

    task_id = int(args[0])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT chat_id, status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()

        if row is None:
            return f"No task #{task_id} found."
        if row["chat_id"] != chat_id:
            return f"No task #{task_id} found."
        if "done" not in ALLOWED_TASK_TRANSITIONS.get(row["status"], set()):
            return f"Task #{task_id} is already '{row['status']}' — cannot mark done."

        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (datetime.datetime.now().isoformat(), task_id),
        )

    return f"Task #{task_id} marked done."


def add_reminder(chat_id: str, args: list[str]) -> str:
    """Schedule a reminder to fire after N minutes.

    Args:
        chat_id: The Telegram chat ID to deliver the reminder to.
        args: Expected as [minutes, *message_words], e.g. ["30", "Take", "a", "break"].

    Returns:
        A confirmation string, or a usage message if the input is malformed.
    """
    if len(args) < 2 or not args[0].isdigit():
        return "Usage: /remind <minutes> <message>"

    minutes = int(args[0])
    message = " ".join(args[1:]).strip()
    if minutes <= 0 or not message:
        return "Usage: /remind <minutes> <message>"

    fire_at = (datetime.datetime.now() + datetime.timedelta(minutes=minutes)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO reminders (chat_id, message, fire_at, delivered) VALUES (?, ?, ?, 0)",
            (chat_id, message, fire_at),
        )

    return f"Reminder set for {minutes} minute{'s' if minutes != 1 else ''} from now: {message}"


def find_pending_tasks_matching(chat_id: str, keyword: str) -> list[dict]:
    """Return pending tasks whose title contains keyword (case-insensitive).

    Used to correlate other modules' alerts (e.g. a price target being hit)
    with a task the user already logged about the same thing.

    Args:
        chat_id: The chat whose tasks to search.
        keyword: Substring to match against task titles, e.g. "gold".

    Returns:
        A list of dicts with keys: id, title, created_at.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at FROM tasks "
            "WHERE chat_id = ? AND status = 'pending' AND title LIKE ? ORDER BY created_at",
            (chat_id, f"%{keyword}%"),
        ).fetchall()
    return [dict(row) for row in rows]


def today_summary(chat_id: str) -> str:
    """Return a plain-text summary of pending tasks and upcoming reminders.

    Args:
        chat_id: The Telegram chat ID requesting the summary.

    Returns:
        A short status string covering pending task count and reminders due
        later today.
    """
    now = datetime.datetime.now()
    end_of_day = now.replace(hour=23, minute=59, second=59).isoformat()

    with get_connection() as conn:
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE chat_id = ? AND status = 'pending'",
            (chat_id,),
        ).fetchone()[0]

        due_today = conn.execute(
            "SELECT message, fire_at FROM reminders "
            "WHERE chat_id = ? AND delivered = 0 AND fire_at <= ? ORDER BY fire_at",
            (chat_id, end_of_day),
        ).fetchall()

    lines = [f"You have {pending_count} pending task{'s' if pending_count != 1 else ''}."]
    if due_today:
        lines.append(f"{len(due_today)} reminder(s) due today:")
        for row in due_today:
            when = datetime.datetime.fromisoformat(row["fire_at"]).strftime("%-I:%M %p")
            lines.append(f"  • {when} — {row['message']}")

    return "\n".join(lines)


def weekly_stats(chat_id: str) -> dict:
    """Return task completion counts for the last 7 days.

    Args:
        chat_id: The chat to compute stats for.

    Returns:
        A dict with keys: completed_count, pending_count, completed_titles.
    """
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()

    with get_connection() as conn:
        completed = conn.execute(
            "SELECT title FROM tasks WHERE chat_id = ? AND status = 'done' AND completed_at >= ?",
            (chat_id, week_ago),
        ).fetchall()
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE chat_id = ? AND status = 'pending'", (chat_id,)
        ).fetchone()[0]

    return {
        "completed_count": len(completed),
        "pending_count": pending_count,
        "completed_titles": [row["title"] for row in completed],
    }


def _format_time(hour: int, minute: int) -> str:
    """Format an hour/minute pair as e.g. '11:00 AM'."""
    return datetime.time(hour, minute).strftime("%-I:%M %p")


def add_daily_reminder(chat_id: str, hour: int, minute: int, message: str) -> str:
    """Set (or replace) this chat's recurring daily reminder.

    Only one daily reminder is active per chat at a time — adding a new one
    while an enabled reminder exists replaces its time and message rather
    than creating a second one, matching "a daily reminder ... unless I
    change it" (one thing you adjust, not a growing list).

    Args:
        chat_id: The owner this reminder belongs to.
        hour: 24-hour clock hour (0-23).
        minute: Minute (0-59).
        message: The text to send each day.

    Returns:
        A confirmation string.
    """
    if not (0 <= hour <= 23 and 0 <= minute <= 59) or not message.strip():
        return "Usage: daily reminder needs a valid time and a message."

    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM daily_reminders WHERE chat_id = ? AND enabled = 1",
            (chat_id,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE daily_reminders SET message = ?, hour = ?, minute = ?, "
                "last_fired_date = NULL WHERE id = ?",
                (message, hour, minute, existing["id"]),
            )
            return (
                f"Updated your daily reminder — now at {_format_time(hour, minute)}: {message}"
            )

        conn.execute(
            "INSERT INTO daily_reminders (chat_id, message, hour, minute, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, message, hour, minute, now),
        )
    return f"Daily reminder set for {_format_time(hour, minute)}: {message}"


def cancel_daily_reminder(chat_id: str) -> str:
    """Disable this chat's active daily reminder, if any.

    Args:
        chat_id: The owner whose daily reminder should be cancelled.

    Returns:
        A confirmation, or a message noting there was nothing to cancel.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE daily_reminders SET enabled = 0 WHERE chat_id = ? AND enabled = 1",
            (chat_id,),
        )
    if cursor.rowcount == 0:
        return "You don't have an active daily reminder."
    return "Daily reminder cancelled."


def daily_reminder_status(chat_id: str) -> str:
    """Describe this chat's active daily reminder, if any.

    Args:
        chat_id: The owner to look up.

    Returns:
        A description of the current daily reminder, or a message noting
        none is set.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT message, hour, minute FROM daily_reminders WHERE chat_id = ? AND enabled = 1",
            (chat_id,),
        ).fetchone()

    if row is None:
        return "No daily reminder set."
    return f"Daily reminder at {_format_time(row['hour'], row['minute'])}: {row['message']}"


def get_due_daily_reminders() -> list[dict]:
    """Return all enabled daily reminders due to fire and not yet sent today.

    A reminder is due once the current time has reached its configured
    hour:minute and it has not already been marked delivered for today's
    date — so a bot restart after the target time still delivers it once
    (late), rather than silently skipping the day.

    Returns:
        A list of dicts with keys: id, chat_id, message.
    """
    now = datetime.datetime.now()
    today = now.date().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, message FROM daily_reminders "
            "WHERE enabled = 1 "
            "AND (last_fired_date IS NULL OR last_fired_date < ?) "
            "AND (hour < ? OR (hour = ? AND minute <= ?))",
            (today, now.hour, now.hour, now.minute),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_daily_reminder_fired(reminder_id: int) -> None:
    """Record today as the last-fired date for a daily reminder.

    Args:
        reminder_id: The ID of the daily_reminders row to mark.
    """
    today = datetime.date.today().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE daily_reminders SET last_fired_date = ? WHERE id = ?",
            (today, reminder_id),
        )


def get_due_reminders() -> list[dict]:
    """Return all undelivered reminders whose fire time has passed.

    Intended to be polled periodically (e.g. by a job queue) and followed by
    mark_delivered() for each one actually sent.

    Returns:
        A list of dicts with keys: id, chat_id, message.
    """
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, message FROM reminders WHERE delivered = 0 AND fire_at <= ?",
            (now,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_delivered(reminder_id: int) -> None:
    """Mark a reminder as delivered so it is not sent again.

    Args:
        reminder_id: The ID of the reminder row to mark.
    """
    with get_connection() as conn:
        conn.execute("UPDATE reminders SET delivered = 1 WHERE id = ?", (reminder_id,))
