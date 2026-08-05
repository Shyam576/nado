"""tests/test_recall.py — keyword search across tasks/expenses/mood/habits."""

import datetime

from modules import recall
from store.db import get_connection


def test_recall_requires_a_keyword():
    assert "Usage" in recall.recall("owner", [])


def test_recall_reports_nothing_found():
    assert "Nothing found" in recall.recall("owner", ["nonexistent"])


def test_recall_finds_matching_task():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (chat_id, title, status, created_at) VALUES (?, 'Sell gold before Diwali', 'pending', ?)",
            ("owner", datetime.datetime.now().isoformat()),
        )

    result = recall.recall("owner", ["gold"])
    assert "Tasks:" in result
    assert "Sell gold before Diwali" in result


def test_recall_finds_matching_expense_by_remarks():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, remarks, category, created_at) "
            "VALUES (?, 5000, 'gold necklace', 'Investment/Gold', ?)",
            ("owner", datetime.datetime.now().isoformat()),
        )

    result = recall.recall("owner", ["gold"])
    assert "Expenses:" in result
    assert "5,000.00 BTN" in result
    assert "gold necklace" in result


def test_recall_finds_matching_mood_note():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO mood_log (chat_id, mood, energy, note, created_at) VALUES (?, 'stressed', 4, 'worried about gold prices', ?)",
            ("owner", datetime.datetime.now().isoformat()),
        )

    result = recall.recall("owner", ["gold"])
    assert "Mood:" in result
    assert "worried about gold prices" in result


def test_recall_finds_matching_habit():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO habit_log (chat_id, habit, completed_at) VALUES (?, 'gold price check', ?)",
            ("owner", datetime.datetime.now().isoformat()),
        )

    result = recall.recall("owner", ["gold"])
    assert "Habits:" in result
    assert "gold price check" in result


def test_recall_is_scoped_to_chat_id():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (chat_id, title, status, created_at) VALUES ('someone_else', 'Sell gold', 'pending', ?)",
            (datetime.datetime.now().isoformat(),),
        )

    assert "Nothing found" in recall.recall("owner", ["gold"])


def test_recall_limits_results_per_category():
    with get_connection() as conn:
        for i in range(10):
            conn.execute(
                "INSERT INTO tasks (chat_id, title, status, created_at) VALUES (?, ?, 'pending', ?)",
                ("owner", f"gold task {i}", datetime.datetime.now().isoformat()),
            )

    result = recall.recall("owner", ["gold"])
    assert result.count("gold task") == recall._MAX_RESULTS_PER_CATEGORY
