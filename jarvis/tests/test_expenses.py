"""tests/test_expenses.py — expense logging, budget math, without hitting the LLM.

_classify_category calls brain._get_llm(), which would load the full GGUF
model — every test here monkeypatches it to a fixed category so the suite
stays fast and doesn't depend on a model being installed.
"""

import datetime

import pytest

from modules import expenses
from store.db import get_connection


@pytest.fixture(autouse=True)
def _no_llm_classification(monkeypatch):
    monkeypatch.setattr(expenses, "_classify_category", lambda recipient, remarks: "Miscellaneous")


def test_add_expense_from_text_happy_path():
    result = expenses.add_expense_from_text("owner", ["500", "lunch", "with", "team"])
    assert "500.00 BTN" in result
    assert "Miscellaneous" in result

    with get_connection() as conn:
        row = conn.execute("SELECT amount, remarks FROM expenses WHERE chat_id = ?", ("owner",)).fetchone()
    assert row["amount"] == 500.0
    assert row["remarks"] == "lunch with team"


def test_add_expense_from_text_rejects_bad_input():
    assert "Usage" in expenses.add_expense_from_text("owner", [])
    assert "Usage" in expenses.add_expense_from_text("owner", ["not-a-number", "lunch"])
    assert "must be positive" in expenses.add_expense_from_text("owner", ["-50", "lunch"])


def _insert_expense(chat_id: str, amount: float, category: str, when: datetime.datetime) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, category, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, amount, category, when.isoformat()),
        )


def test_budget_status_reports_remaining():
    expenses.set_budget("owner", ["1000"])
    _insert_expense("owner", 300.0, "Food", datetime.datetime.now())

    result = expenses.budget_status("owner")
    assert "300.00 BTN spent across 1 expense" in result
    assert "700.00 remaining" in result


def test_budget_status_reports_over_budget():
    expenses.set_budget("owner", ["100"])
    _insert_expense("owner", 150.0, "Food", datetime.datetime.now())

    result = expenses.budget_status("owner")
    assert "over by 50.00" in result


def test_budget_status_without_a_budget_set():
    _insert_expense("owner", 200.0, "Food", datetime.datetime.now())
    result = expenses.budget_status("owner")
    assert "Budget:" not in result


def test_budget_status_excludes_previous_month():
    last_month = datetime.datetime.now().replace(day=1) - datetime.timedelta(days=1)
    _insert_expense("owner", 999.0, "Food", last_month)

    result = expenses.budget_status("owner")
    assert "0.00 BTN spent across 0 expenses" in result


def test_set_budget_validation():
    assert "Usage" in expenses.set_budget("owner", [])
    assert "Usage" in expenses.set_budget("owner", ["not-a-number"])
    assert "must be positive" in expenses.set_budget("owner", ["-5"])

    assert "set to 1,500.00" in expenses.set_budget("owner", ["1500"])
    assert "cleared" in expenses.set_budget("owner", ["clear"])


def test_weekly_expense_summary_aggregates_by_category():
    now = datetime.datetime.now()
    _insert_expense("owner", 100.0, "Food", now)
    _insert_expense("owner", 50.0, "Food", now)
    _insert_expense("owner", 200.0, "Transport", now)
    _insert_expense("owner", 999.0, "Food", now - datetime.timedelta(days=10))  # outside window

    summary = expenses.weekly_expense_summary("owner")
    assert summary["total"] == 350.0
    assert summary["by_category"]["Food"] == 150.0
    assert summary["by_category"]["Transport"] == 200.0


def test_set_category_rejects_unknown_category():
    expenses.add_expense_from_text("owner", ["500", "lunch"])
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM expenses WHERE chat_id = ?", ("owner",)).fetchone()

    outcome = expenses.set_category("owner", [str(row["id"]), "NotARealCategory"])
    assert "Unknown category" in outcome


def test_set_category_rejects_wrong_owner():
    expenses.add_expense_from_text("owner", ["500", "lunch"])
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM expenses WHERE chat_id = ?", ("owner",)).fetchone()

    outcome = expenses.set_category("someone_else", [str(row["id"]), "Food"])
    assert "No expense" in outcome


def test_correct_expense_fixes_amount_and_category_via_description():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, category, created_at) VALUES (?, NULL, 'Miscellaneous', ?)",
            ("owner", datetime.datetime.now().isoformat()),
        )
        expense_id = conn.execute("SELECT id FROM expenses WHERE chat_id = ?", ("owner",)).fetchone()[0]

    result = expenses.correct_expense("owner", None, 150.0, "vegetables")
    assert f"Expense #{expense_id} amount corrected to 150.00" in result
    # _classify_category is mocked to always return "Miscellaneous" in this
    # file (see _no_llm_classification fixture) — real classification into
    # "Food" was already verified manually against the live LLM.
    assert f"Expense #{expense_id} updated: vegetables [Miscellaneous]" in result

    with get_connection() as conn:
        row = conn.execute(
            "SELECT amount, remarks, category FROM expenses WHERE id = ?", (expense_id,)
        ).fetchone()
    assert row["amount"] == 150.0
    assert row["remarks"] == "vegetables"


def test_correct_expense_defaults_to_most_recent_when_id_omitted():
    expenses.add_expense_from_text("owner", ["100", "old", "one"])
    expenses.add_expense_from_text("owner", ["200", "newest", "one"])
    with get_connection() as conn:
        newest_id = conn.execute(
            "SELECT id FROM expenses WHERE chat_id = ? ORDER BY created_at DESC LIMIT 1", ("owner",)
        ).fetchone()[0]

    result = expenses.correct_expense("owner", None, 250.0, None)
    assert f"Expense #{newest_id} amount corrected to 250.00" in result


def test_correct_expense_requires_amount_or_description():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, category, created_at) VALUES (?, 100, 'Food', ?)",
            ("owner", datetime.datetime.now().isoformat()),
        )
    assert "Tell me what to change" in expenses.correct_expense("owner", None, None, None)


def test_correct_expense_reports_no_expenses_yet():
    assert "don't have any expenses" in expenses.correct_expense("owner", None, 100.0, "lunch")


def test_correct_expense_rejects_wrong_owner_for_description():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, category, created_at) VALUES (?, 100, 'Food', ?)",
            ("someone_else", datetime.datetime.now().isoformat()),
        )
        expense_id = conn.execute(
            "SELECT id FROM expenses WHERE chat_id = ?", ("someone_else",)
        ).fetchone()[0]

    result = expenses.correct_expense("owner", expense_id, None, "vegetables")
    assert "No expense" in result
