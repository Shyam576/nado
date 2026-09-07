"""tests/test_execution_intent.py — the plan/review/weekly-status NL intent dispatch."""

from modules import execution, intent


def test_dispatch_set_daily_priorities_adds_each_in_order():
    reply = intent._dispatch_set_daily_priorities(
        "owner", {"priorities": ["Fix deployment", "Review PR", "Send client response"]}
    )
    assert "Added 3 priorities" in reply.text

    priorities = execution.get_priorities("owner")
    assert [p["title"] for p in priorities] == ["Fix deployment", "Review PR", "Send client response"]


def test_dispatch_set_daily_priorities_is_additive_not_destructive():
    execution.add_priority("owner", "Existing task")
    intent._dispatch_set_daily_priorities("owner", {"priorities": ["New task"]})

    titles = [p["title"] for p in execution.get_priorities("owner")]
    assert "Existing task" in titles
    assert "New task" in titles


def test_dispatch_set_daily_priorities_rejects_empty_list():
    assert intent._dispatch_set_daily_priorities("owner", {"priorities": []}) is None
    assert intent._dispatch_set_daily_priorities("owner", {}) is None


def test_dispatch_set_daily_priorities_skips_blank_strings():
    reply = intent._dispatch_set_daily_priorities("owner", {"priorities": ["Real task", "   "]})
    assert "Added 1 priority" in reply.text
    assert len(execution.get_priorities("owner")) == 1


def test_dispatch_submit_daily_review_passes_extracted_fields():
    reply = intent._dispatch_submit_daily_review(
        "owner",
        {
            "punctual": True,
            "worked_by_priority": True,
            "execution_score": 7,
            "adjustment_for_tomorrow": "start earlier",
            "actual_start_time": None,
        },
    )
    assert "logged today's review" in reply.text

    plan = execution.get_or_create_daily_plan("owner")
    assert plan["punctual"] == 1
    assert plan["execution_score"] == 7
    assert plan["adjustment_for_tomorrow"] == "start earlier"
    assert plan["review_completed"] == 1


def test_dispatch_submit_daily_review_ignores_invalid_score():
    intent._dispatch_submit_daily_review("owner", {"punctual": True, "execution_score": 99})
    plan = execution.get_or_create_daily_plan("owner")
    assert plan["punctual"] == 1
    assert plan["execution_score"] is None


def test_dispatch_submit_daily_review_falls_through_when_nothing_extracted():
    assert intent._dispatch_submit_daily_review("owner", {}) is None
    assert intent._dispatch_submit_daily_review(
        "owner", {"punctual": None, "execution_score": None}
    ) is None


def test_plan_day_and_weekly_status_intents_are_registered():
    assert intent._DISPATCH["plan_day"]("owner", {}).text.startswith("Today")
    assert "Punctuality" in intent._DISPATCH["weekly_status"]("owner", {}).text


def test_review_day_intent_is_registered():
    execution.add_priority("owner", "Fix deployment")
    reply = intent._DISPATCH["review_day"]("owner", {})
    assert "Fix deployment" in reply.text
