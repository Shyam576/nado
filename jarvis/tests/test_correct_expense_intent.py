"""tests/test_correct_expense_intent.py — the "correct_expense" NL intent dispatch."""

from modules import expenses, intent


def test_dispatch_correct_expense_passes_through_fields(monkeypatch):
    captured = {}

    def _fake_correct_expense(chat_id, expense_id, amount, description):
        captured.update(chat_id=chat_id, expense_id=expense_id, amount=amount, description=description)
        return "ok"

    monkeypatch.setattr(expenses, "correct_expense", _fake_correct_expense)

    reply = intent._dispatch_correct_expense(
        "owner", {"expense_id": 46, "amount": 150, "description": "drinks"}
    )
    assert reply.text == "ok"
    assert captured == {"chat_id": "owner", "expense_id": 46, "amount": 150, "description": "drinks"}


def test_dispatch_correct_expense_defaults_null_fields(monkeypatch):
    captured = {}

    def _fake_correct_expense(chat_id, expense_id, amount, description):
        captured.update(expense_id=expense_id, amount=amount, description=description)
        return "ok"

    monkeypatch.setattr(expenses, "correct_expense", _fake_correct_expense)

    intent._dispatch_correct_expense("owner", {"expense_id": None, "amount": None, "description": None})
    assert captured == {"expense_id": None, "amount": None, "description": None}


def test_dispatch_correct_expense_rejects_non_numeric_expense_id():
    assert intent._dispatch_correct_expense("owner", {"expense_id": "not a number"}) is None


def test_dispatch_correct_expense_rejects_non_numeric_amount():
    assert intent._dispatch_correct_expense("owner", {"amount": "a lot"}) is None
