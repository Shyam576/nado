"""tests/test_execution.py — daily plans, evening review, weekly outcomes,
metrics, and the mentor summary (LLM mocked, same pattern as test_vision.py)."""

import datetime

import brain
from modules import execution


def _monday() -> str:
    """Return the ISO date of the Monday of the current week (stable regardless of today)."""
    return execution.week_start_for(datetime.date.today().isoformat())


# ---------------------------------------------------------------------------
# Daily plan — morning
# ---------------------------------------------------------------------------


def test_get_or_create_daily_plan_is_idempotent():
    first = execution.get_or_create_daily_plan("owner", "2026-09-07")
    second = execution.get_or_create_daily_plan("owner", "2026-09-07")
    assert first["id"] == second["id"]


def test_set_morning_plan_updates_target_and_commitment():
    plan = execution.set_morning_plan(
        "owner", "2026-09-07", target_start_time="09:00", must_not_slip="Ship the PR"
    )
    assert plan["target_start_time"] == "09:00"
    assert plan["must_not_slip"] == "Ship the PR"


def test_add_priority_appends_in_order():
    execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    execution.add_priority("owner", "Review PR", date="2026-09-07")

    priorities = execution.get_priorities("owner", "2026-09-07")
    assert [p["title"] for p in priorities] == ["Fix deployment", "Review PR"]
    assert [p["priority_order"] for p in priorities] == [0, 1]


def test_edit_priority_renames():
    p = execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    updated = execution.edit_priority("owner", p["id"], "Fix production deployment")
    assert updated["title"] == "Fix production deployment"


def test_edit_priority_rejects_other_owners_priority():
    p = execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    assert execution.edit_priority("someone-else", p["id"], "hijacked") is None


def test_reorder_priorities():
    a = execution.add_priority("owner", "A", date="2026-09-07")
    b = execution.add_priority("owner", "B", date="2026-09-07")
    c = execution.add_priority("owner", "C", date="2026-09-07")

    reordered = execution.reorder_priorities("owner", "2026-09-07", [c["id"], a["id"], b["id"]])
    assert [p["title"] for p in reordered] == ["C", "A", "B"]


def test_complete_priority_sets_status_and_timestamp():
    p = execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    updated = execution.complete_priority("owner", p["id"])
    assert updated["status"] == "done"
    assert updated["completed_at"] is not None


def test_uncomplete_priority_reverts_to_pending():
    p = execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    execution.complete_priority("owner", p["id"])
    reverted = execution.uncomplete_priority("owner", p["id"])
    assert reverted["status"] == "pending"
    assert reverted["completed_at"] is None


def test_uncomplete_priority_rejects_other_owners_priority():
    p = execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    assert execution.uncomplete_priority("someone-else", p["id"]) is None


def test_carry_forward_priority_marks_original_and_recreates_on_next_day():
    p = execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    new_row = execution.carry_forward_priority("owner", p["id"], reason="ran out of time")

    original = execution.get_priorities("owner", "2026-09-07")[0]
    assert original["status"] == "carried_forward"
    assert original["carried_forward"] == 1
    assert original["carry_forward_reason"] == "ran out of time"

    assert new_row["title"] == "Fix deployment"
    tomorrow = execution.get_priorities("owner", "2026-09-08")
    assert len(tomorrow) == 1
    assert tomorrow[0]["id"] == new_row["id"]


def test_get_today_bundles_plan_and_priorities():
    execution.set_morning_plan("owner", "2026-09-07", target_start_time="09:00")
    execution.add_priority("owner", "Fix deployment", date="2026-09-07")

    today = execution.get_today("owner", "2026-09-07")
    assert today["plan"]["target_start_time"] == "09:00"
    assert len(today["priorities"]) == 1


# ---------------------------------------------------------------------------
# Evening review
# ---------------------------------------------------------------------------


def test_submit_evening_review_sets_fields_and_completes():
    review = execution.submit_evening_review(
        "owner",
        "2026-09-07",
        actual_start_time="09:15",
        punctual=False,
        worked_by_priority=True,
        execution_score=7,
        adjustment_for_tomorrow="Start 15 minutes earlier",
    )
    assert review["actual_start_time"] == "09:15"
    assert review["punctual"] == 0
    assert review["worked_by_priority"] == 1
    assert review["execution_score"] == 7
    assert review["review_completed"] == 1


def test_submit_evening_review_rejects_invalid_score():
    import pytest

    with pytest.raises(ValueError):
        execution.submit_evening_review("owner", "2026-09-07", execution_score=11)


def test_submit_evening_review_partial_update_preserves_other_fields():
    execution.submit_evening_review("owner", "2026-09-07", execution_score=6)
    updated = execution.submit_evening_review("owner", "2026-09-07", punctual=True)
    assert updated["execution_score"] == 6
    assert updated["punctual"] == 1


# ---------------------------------------------------------------------------
# Weekly outcomes
# ---------------------------------------------------------------------------


def test_add_weekly_outcome_and_list():
    week = _monday()
    execution.add_weekly_outcome("owner", "Resolve deployment problem", week_start=week)
    execution.add_weekly_outcome("owner", "Complete architecture doc", week_start=week)

    outcomes = execution.get_weekly_outcomes("owner", week)
    assert [o["title"] for o in outcomes] == ["Resolve deployment problem", "Complete architecture doc"]
    assert all(o["status"] == "pending" for o in outcomes)


def test_update_weekly_outcome_status_to_done_sets_completed_at():
    week = _monday()
    outcome = execution.add_weekly_outcome("owner", "Resolve deployment problem", week_start=week)
    updated = execution.update_weekly_outcome_status("owner", outcome["id"], "done")
    assert updated["status"] == "done"
    assert updated["completed_at"] is not None


def test_weekly_outcome_shows_related_daily_priorities():
    week = _monday()
    outcome = execution.add_weekly_outcome("owner", "Resolve deployment problem", week_start=week)
    execution.add_priority("owner", "Fix deployment", date=week, weekly_outcome_id=outcome["id"])

    outcomes = execution.get_weekly_outcomes("owner", week)
    assert len(outcomes[0]["related_priorities"]) == 1
    assert outcomes[0]["related_priorities"][0]["title"] == "Fix deployment"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_compute_week_metrics_punctuality_and_completion():
    week = _monday()
    day1, day2 = week, (datetime.date.fromisoformat(week) + datetime.timedelta(days=1)).isoformat()

    p1 = execution.add_priority("owner", "A", date=day1)
    execution.add_priority("owner", "B", date=day1)
    execution.complete_priority("owner", p1["id"])
    execution.submit_evening_review("owner", day1, punctual=True)

    execution.add_priority("owner", "C", date=day2)
    execution.submit_evening_review("owner", day2, punctual=False)

    metrics = execution.compute_week_metrics("owner", week)
    assert metrics["punctuality_pct"] == 50.0
    assert round(metrics["completion_pct"], 2) == round(100 * 1 / 3, 2)
    assert metrics["reviews_completed"] == 2


def test_compute_week_metrics_avg_execution_score():
    week = _monday()
    day1, day2 = week, (datetime.date.fromisoformat(week) + datetime.timedelta(days=1)).isoformat()
    execution.submit_evening_review("owner", day1, execution_score=6)
    execution.submit_evening_review("owner", day2, execution_score=8)

    metrics = execution.compute_week_metrics("owner", week)
    assert metrics["avg_execution_score"] == 7.0


def test_compute_week_metrics_empty_week_returns_none_percentages():
    metrics = execution.compute_week_metrics("owner", "2026-09-07")
    assert metrics["punctuality_pct"] is None
    assert metrics["avg_execution_score"] is None
    assert metrics["completion_pct"] is None
    assert metrics["carry_forward_count"] == 0


def test_compute_week_metrics_counts_carry_forward():
    week = _monday()
    p = execution.add_priority("owner", "A", date=week)
    execution.carry_forward_priority("owner", p["id"], reason="blocked")

    metrics = execution.compute_week_metrics("owner", week)
    assert metrics["carry_forward_count"] == 1


def test_submit_weekly_review_snapshots_metrics_and_is_idempotent():
    week = _monday()
    p = execution.add_priority("owner", "A", date=week)
    execution.complete_priority("owner", p["id"])
    execution.submit_evening_review("owner", week, punctual=True)

    review = execution.submit_weekly_review(
        "owner", week, went_well="Shipped the fix", pattern_observed="Rushed mornings"
    )
    assert review["went_well"] == "Shipped the fix"
    assert review["completion_pct"] == 100.0

    updated = execution.submit_weekly_review("owner", week, went_well="Shipped the fix and more")
    assert updated["id"] == review["id"]  # upsert, not a duplicate row
    assert updated["went_well"] == "Shipped the fix and more"
    assert updated["pattern_observed"] == "Rushed mornings"  # untouched field preserved (COALESCE)


def test_get_week_bundles_outcomes_scorecard_and_review():
    week = _monday()
    execution.add_weekly_outcome("owner", "Ship the thing", week_start=week)
    result = execution.get_week("owner", week)
    assert result["week_start"] == week
    assert len(result["outcomes"]) == 1
    assert result["review"] is None
    assert result["scorecard"]["weekly_review_done"] is False


# ---------------------------------------------------------------------------
# Development cycle / progress
# ---------------------------------------------------------------------------


def test_create_cycle_deactivates_previous_active_cycle():
    first = execution.create_cycle("owner", "Cycle 1", "2026-08-01", "2026-09-30")
    second = execution.create_cycle("owner", "Cycle 2", "2026-10-01", "2026-11-30")

    from store.db import get_connection

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM development_cycles WHERE id = ?", (first["id"],)).fetchone()
    assert row["status"] == "completed"
    assert second["status"] == "active"
    assert execution.get_active_cycle("owner")["id"] == second["id"]


def test_get_progress_returns_one_row_per_week_in_cycle():
    week = _monday()
    cycle_start = (datetime.date.fromisoformat(week) - datetime.timedelta(days=7)).isoformat()
    execution.create_cycle("owner", "Cycle", cycle_start, week)

    progress = execution.get_progress("owner")
    assert len(progress) == 2
    assert progress[0]["week_start"] < progress[1]["week_start"]


def test_get_progress_empty_without_a_cycle():
    assert execution.get_progress("no-such-owner") == []


# ---------------------------------------------------------------------------
# Mentor summary (LLM mocked — same pattern as tests/test_vision.py)
# ---------------------------------------------------------------------------


def test_generate_mentor_summary_without_any_data():
    assert "Not enough data" in execution.generate_mentor_summary("owner")


def test_generate_mentor_summary_calls_llm_with_metrics(monkeypatch):
    week = _monday()
    p = execution.add_priority("owner", "A", date=week)
    execution.complete_priority("owner", p["id"])
    execution.submit_evening_review("owner", week, punctual=True)
    cycle_start = (datetime.date.fromisoformat(week) - datetime.timedelta(days=7)).isoformat()
    execution.create_cycle("owner", "Cycle", cycle_start, week)

    captured = {}

    class _FakeLLM:
        def create_chat_completion(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return {"choices": [{"message": {"content": "Since the previous session: improved."}}]}

    monkeypatch.setattr(brain, "_get_llm", lambda: _FakeLLM())

    summary = execution.generate_mentor_summary("owner")
    assert summary == "Since the previous session: improved."
    assert "Punctuality" in captured["messages"][1]["content"]


def test_generate_mentor_summary_handles_llm_failure(monkeypatch):
    week = _monday()
    execution.add_priority("owner", "A", date=week)
    cycle_start = (datetime.date.fromisoformat(week) - datetime.timedelta(days=7)).isoformat()
    execution.create_cycle("owner", "Cycle", cycle_start, week)

    class _BoomLLM:
        def create_chat_completion(self, **kwargs):
            raise RuntimeError("model not loaded")

    monkeypatch.setattr(brain, "_get_llm", lambda: _BoomLLM())

    summary = execution.generate_mentor_summary("owner")
    assert "Couldn't generate" in summary


# ---------------------------------------------------------------------------
# Chat-text formatting (shared by bot commands + conversational intents)
# ---------------------------------------------------------------------------


def test_add_priorities_bulk_appends_all_in_order():
    rows = execution.add_priorities("owner", ["Fix deployment", "Review PR", "Send client response"], date="2026-09-07")
    assert [r["title"] for r in rows] == ["Fix deployment", "Review PR", "Send client response"]
    assert [r["priority_order"] for r in rows] == [0, 1, 2]


def test_add_priorities_skips_blank_entries():
    rows = execution.add_priorities("owner", ["Fix deployment", "  ", ""], date="2026-09-07")
    assert len(rows) == 1


def test_describe_today_prompts_when_no_priorities():
    text = execution.describe_today("owner")
    assert "No priorities set yet" in text


def test_describe_today_lists_priorities_with_status_marks():
    p = execution.add_priority("owner", "Fix deployment", date="2026-09-07")
    execution.complete_priority("owner", p["id"])
    execution.add_priority("owner", "Review PR", date="2026-09-07")

    text = execution.describe_today("owner")
    assert "✓ 1. Fix deployment" in text
    assert "• 2. Review PR" in text


def test_review_prompt_shows_status_and_asks_for_review():
    execution.add_priority("owner", "Fix deployment", date=execution._today())
    text = execution.review_prompt("owner")
    assert "Fix deployment" in text
    assert "Tell me how it went" in text


def test_review_prompt_shows_existing_review_instead_of_reprompting():
    execution.submit_evening_review("owner", execution_score=7)
    text = execution.review_prompt("owner")
    assert "already done" in text


def test_describe_week_includes_scorecard_and_outcomes():
    week = _monday()
    execution.add_weekly_outcome("owner", "Ship the fix", week_start=week)
    text = execution.describe_week("owner")
    assert "Punctuality" in text
    assert "Ship the fix" in text


# ---------------------------------------------------------------------------
# Mentorship context (5Ws + 2Hs)
# ---------------------------------------------------------------------------


def test_get_mentorship_context_defaults_without_cycle_or_saved_fields():
    ctx = execution.get_mentorship_context("owner")
    assert ctx["development_action"] is None
    assert ctx["what"] is None


def test_get_mentorship_context_pulls_development_action_from_active_cycle():
    execution.create_cycle("owner", "Cycle", "2026-09-01", "2026-11-01", development_action="Improve execution discipline")
    ctx = execution.get_mentorship_context("owner")
    assert ctx["development_action"] == "Improve execution discipline"


def test_set_mentorship_context_saves_and_preserves_unspecified_fields():
    execution.set_mentorship_context("owner", what="Improve punctuality", why="Feedback from leadership")
    execution.set_mentorship_context("owner", when="8-12 week cycle")

    ctx = execution.get_mentorship_context("owner")
    assert ctx["what"] == "Improve punctuality"
    assert ctx["why"] == "Feedback from leadership"
    assert ctx["when"] == "8-12 week cycle"
