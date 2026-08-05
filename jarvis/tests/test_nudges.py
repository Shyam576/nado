"""tests/test_nudges.py — budget pacing, stale-task, and related-task nudges."""

import datetime

import memory
from modules import nudges, tasks
from store.db import get_connection


def _insert_expense(chat_id: str, amount: float, when: datetime.datetime) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, created_at) VALUES (?, ?, ?)",
            (chat_id, amount, when.isoformat()),
        )


def test_budget_pacing_alert_noop_without_budget():
    assert nudges.budget_pacing_alert("owner") is None


def test_budget_pacing_alert_fires_at_80_percent():
    memory.set_preference("monthly_budget", 1000.0)
    _insert_expense("owner", 850.0, datetime.datetime.now())

    alert = nudges.budget_pacing_alert("owner")
    assert alert is not None
    assert "80%" in alert

    # Same threshold shouldn't refire this month
    assert nudges.budget_pacing_alert("owner") is None


def test_budget_pacing_alert_fires_at_100_percent_with_distinct_message():
    # Thresholds fire in order as spend crosses them (80% first, then 100%),
    # matching how this is actually polled hourly rather than checked once
    # at a single final total.
    memory.set_preference("monthly_budget", 1000.0)
    _insert_expense("owner", 850.0, datetime.datetime.now())
    nudges.budget_pacing_alert("owner")  # consumes the 80% threshold

    _insert_expense("owner", 200.0, datetime.datetime.now())  # now past 100%
    alert = nudges.budget_pacing_alert("owner")
    assert alert is not None
    assert "hit your monthly budget" in alert


def test_evening_expense_nudge_only_when_nothing_logged_today():
    assert nudges.evening_expense_nudge("owner") is not None

    _insert_expense("owner", 50.0, datetime.datetime.now())
    assert nudges.evening_expense_nudge("owner") is None


def test_stale_task_nudge_fires_once_per_task():
    old = (datetime.datetime.now() - datetime.timedelta(days=6)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (chat_id, title, status, created_at) VALUES (?, 'Old task', 'pending', ?)",
            ("owner", old),
        )

    first = nudges.stale_task_nudge("owner")
    assert first is not None
    assert "Old task" in first

    # Already nudged — should not repeat for the same task
    assert nudges.stale_task_nudge("owner") is None


def test_stale_task_nudge_silent_for_recent_tasks():
    tasks.add_task("owner", ["Fresh", "task"])
    assert nudges.stale_task_nudge("owner") is None


def test_related_task_note_finds_matching_pending_task():
    tasks.add_task("owner", ["Sell", "gold", "before", "Diwali"])

    note = nudges.related_task_note("owner", "gold")
    assert "Sell gold before Diwali" in note
    assert note.startswith("\n\nRelated open task(s):")


def test_related_task_note_empty_when_no_match():
    tasks.add_task("owner", ["Buy", "groceries"])
    assert nudges.related_task_note("owner", "gold") == ""
