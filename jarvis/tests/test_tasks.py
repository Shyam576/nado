"""tests/test_tasks.py — task state machine, reminders, daily reminders."""

import datetime

from modules import tasks
from store.db import get_connection


def _extract_task_id(reply: str) -> str:
    """Pull the numeric ID out of an add_task() confirmation like 'Task #3 added: ...'."""
    return reply.split("#")[1].split()[0]


def test_add_and_list_tasks():
    tasks.add_task("owner", ["Buy", "groceries"])
    tasks.add_task("owner", ["Renew", "passport"])

    result = tasks.list_tasks("owner")
    assert "Buy groceries" in result
    assert "Renew passport" in result


def test_list_tasks_empty():
    assert "No pending tasks" in tasks.list_tasks("owner")


def test_add_task_requires_title():
    assert "Usage" in tasks.add_task("owner", [])


def test_complete_task_happy_path():
    reply = tasks.add_task("owner", ["Buy", "milk"])
    task_id = int(_extract_task_id(reply))

    result = tasks.complete_task("owner", [str(task_id)])
    assert "marked done" in result

    # Pending list should no longer include it
    assert "Buy milk" not in tasks.list_tasks("owner")


def test_complete_task_cannot_redo_a_done_task():
    reply = tasks.add_task("owner", ["Buy", "milk"])
    task_id = str(_extract_task_id(reply))

    tasks.complete_task("owner", [task_id])
    result = tasks.complete_task("owner", [task_id])
    assert "already 'done'" in result


def test_complete_task_rejects_wrong_owner():
    reply = tasks.add_task("owner", ["Buy", "milk"])
    task_id = str(_extract_task_id(reply))

    result = tasks.complete_task("someone_else", [task_id])
    assert "No task" in result

    # Task should still be pending for the real owner
    assert "Buy milk" in tasks.list_tasks("owner")


def test_complete_task_rejects_unknown_id():
    assert "No task #999" in tasks.complete_task("owner", ["999"])


def test_complete_task_rejects_non_numeric_id():
    assert "Usage" in tasks.complete_task("owner", ["abc"])


def test_find_pending_tasks_matching_is_case_insensitive_and_owner_scoped():
    tasks.add_task("owner", ["Sell", "gold", "before", "Diwali"])
    tasks.add_task("owner", ["Buy", "groceries"])
    tasks.add_task("someone_else", ["Sell", "GOLD", "coins"])

    matches = tasks.find_pending_tasks_matching("owner", "gold")
    assert len(matches) == 1
    assert matches[0]["title"] == "Sell gold before Diwali"


def test_find_pending_tasks_matching_excludes_completed():
    reply = tasks.add_task("owner", ["Sell", "gold"])
    task_id = str(_extract_task_id(reply))
    tasks.complete_task("owner", [task_id])

    assert tasks.find_pending_tasks_matching("owner", "gold") == []


def test_add_reminder_validation():
    assert "Usage" in tasks.add_reminder("owner", [])
    assert "Usage" in tasks.add_reminder("owner", ["notanumber", "call", "mom"])
    assert "Usage" in tasks.add_reminder("owner", ["0", "call", "mom"])  # non-positive
    assert "Reminder set" in tasks.add_reminder("owner", ["30", "call", "mom"])


def test_weekly_stats_counts_completed_and_pending():
    reply = tasks.add_task("owner", ["Task", "A"])
    task_a_id = str(_extract_task_id(reply))
    tasks.add_task("owner", ["Task", "B"])
    tasks.complete_task("owner", [task_a_id])

    stats = tasks.weekly_stats("owner")
    assert stats["completed_count"] == 1
    assert stats["pending_count"] == 1
    assert stats["completed_titles"] == ["Task A"]


def test_weekly_stats_excludes_completions_older_than_7_days():
    with get_connection() as conn:
        old = (datetime.datetime.now() - datetime.timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO tasks (chat_id, title, status, created_at, completed_at) "
            "VALUES (?, ?, 'done', ?, ?)",
            ("owner", "Old task", old, old),
        )

    stats = tasks.weekly_stats("owner")
    assert stats["completed_count"] == 0


def test_add_daily_reminder_replaces_existing_one():
    tasks.add_daily_reminder("owner", 9, 0, "Stand up meeting")
    result = tasks.add_daily_reminder("owner", 14, 30, "Stretch")

    assert "Updated your daily reminder" in result
    status = tasks.daily_reminder_status("owner")
    assert "2:30 PM" in status
    assert "Stretch" in status
    assert "Stand up meeting" not in status  # replaced, not duplicated

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_reminders WHERE chat_id = ?", ("owner",)
        ).fetchone()[0]
    assert count == 1


def test_cancel_daily_reminder():
    tasks.add_daily_reminder("owner", 9, 0, "Stand up meeting")
    result = tasks.cancel_daily_reminder("owner")
    assert result == "Daily reminder cancelled."
    assert "No daily reminder" in tasks.daily_reminder_status("owner")


def test_cancel_daily_reminder_with_none_active():
    assert "don't have an active daily reminder" in tasks.cancel_daily_reminder("owner")
