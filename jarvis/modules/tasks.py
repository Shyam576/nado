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
