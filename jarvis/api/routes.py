"""
api/routes.py — JSON API for the web dashboard.

Thin HTTP wrapper around the modules/*.py business-logic layer (mainly
execution.py, plus devops/expenses/finance/tasks/calendar_app for the
read-only Life page and header strip) — every handler here does request
validation (via api/models.py) and response shaping, nothing else. No
business logic lives in this file; it belongs in the modules so the bot
transports (bot/commands.py, modules/intent.py) and the dashboard call the
exact same functions against the exact same tables. See
modules/execution.py's module docstring.

All routes are scoped to config.OWNER_ID, same as the bot transports — the
dashboard is a single-user surface onto the same one owner's data
regardless of which platform (Telegram, Discord, browser) touched it.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_session
from api.models import (
    CarryForwardIn,
    CycleIn,
    CycleOut,
    DailyPlanOut,
    EveningReviewIn,
    HeaderOut,
    MentorshipContextIn,
    MentorshipContextOut,
    MentorSummaryOut,
    MoneyOut,
    MoodOut,
    MorningPlanIn,
    OutcomeStatusIn,
    PeopleGlanceOut,
    PriorityEditIn,
    PriorityIn,
    PriorityOut,
    ProgressOut,
    ReorderIn,
    ReviewSuggestionsOut,
    SystemOut,
    TodayContextOut,
    TodayOut,
    WeeklyOutcomeIn,
    WeeklyOutcomeOut,
    WeeklyReviewIn,
    WeeklyReviewOut,
    WeeklyReviewSuggestionsOut,
    WeekOut,
)
from config import OWNER_ID
from modules import calendar_app, devops, execution, expenses, finance, habits, people, tasks

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


# ---------------------------------------------------------------------------
# Header strip — loaded on every page, kept to one round-trip
# ---------------------------------------------------------------------------


@router.get("/header", response_model=HeaderOut)
def get_header():
    plan = execution.get_or_create_daily_plan(OWNER_ID)
    next_reminder = tasks.get_next_reminder(OWNER_ID)
    k8s = devops.k8s_health_summary()
    return {
        "date": plan["date"],
        "must_not_slip": plan["must_not_slip"],
        "next_reminder": next_reminder,
        "k8s_healthy": k8s["healthy"],
        "k8s_unhealthy_count": k8s["unhealthy_count"],
    }


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------


@router.get("/today", response_model=TodayOut)
def get_today(date: Optional[str] = None):
    return execution.get_today(OWNER_ID, date)


@router.post("/today/morning-plan", response_model=DailyPlanOut)
def set_morning_plan(body: MorningPlanIn):
    return execution.set_morning_plan(
        OWNER_ID, date=body.date, target_start_time=body.target_start_time, must_not_slip=body.must_not_slip
    )


@router.post("/today/priorities", response_model=PriorityOut)
def add_priority(body: PriorityIn):
    return execution.add_priority(
        OWNER_ID, body.title, date=body.date, weekly_outcome_id=body.weekly_outcome_id
    )


@router.patch("/today/priorities/{priority_id}", response_model=PriorityOut)
def edit_priority(priority_id: int, body: PriorityEditIn):
    row = execution.edit_priority(OWNER_ID, priority_id, body.title)
    if row is None:
        raise HTTPException(status_code=404, detail="Priority not found")
    return row


@router.post("/today/priorities/reorder", response_model=list[PriorityOut])
def reorder_priorities(body: ReorderIn):
    return execution.reorder_priorities(OWNER_ID, body.date, body.ordered_priority_ids)


@router.post("/today/priorities/{priority_id}/complete", response_model=PriorityOut)
def complete_priority(priority_id: int):
    row = execution.complete_priority(OWNER_ID, priority_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Priority not found")
    return row


@router.post("/today/priorities/{priority_id}/uncomplete", response_model=PriorityOut)
def uncomplete_priority(priority_id: int):
    row = execution.uncomplete_priority(OWNER_ID, priority_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Priority not found")
    return row


@router.post("/today/priorities/{priority_id}/carry-forward", response_model=PriorityOut)
def carry_forward_priority(priority_id: int, body: CarryForwardIn):
    row = execution.carry_forward_priority(
        OWNER_ID, priority_id, reason=body.reason, to_date=body.to_date
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Priority not found")
    return row


@router.post("/today/evening-review", response_model=DailyPlanOut)
def submit_evening_review(body: EveningReviewIn):
    return execution.submit_evening_review(
        OWNER_ID,
        date=body.date,
        actual_start_time=body.actual_start_time,
        punctual=body.punctual,
        worked_by_priority=body.worked_by_priority,
        execution_score=body.execution_score,
        adjustment_for_tomorrow=body.adjustment_for_tomorrow,
    )


@router.get("/today/context", response_model=TodayContextOut)
def get_today_context():
    """Read-only context so the morning plan doesn't need a Discord round-trip:
    pending tasks and today's calendar. No write path here — task completion
    and calendar entries are still owned by chat/voice, not this dashboard."""
    return {
        "pending_tasks": tasks.pending_tasks(OWNER_ID),
        "calendar_events": calendar_app.get_today_events(),
    }


@router.get("/today/review-suggestions", response_model=ReviewSuggestionsOut)
def get_review_suggestions(date: Optional[str] = None):
    """Pre-fills for the evening review wizard — every field here is a
    suggestion the wizard shows as editable/overridable, never auto-applied."""
    suggestions = execution.get_review_suggestions(OWNER_ID, date)
    suggestions["carry_forward_reasons"] = execution.CARRY_FORWARD_REASONS
    return suggestions


# ---------------------------------------------------------------------------
# Week
# ---------------------------------------------------------------------------


@router.get("/week", response_model=WeekOut)
def get_week(week_start: Optional[str] = None):
    return execution.get_week(OWNER_ID, week_start)


@router.post("/week/outcomes", response_model=WeeklyOutcomeOut)
def add_weekly_outcome(body: WeeklyOutcomeIn):
    outcome = execution.add_weekly_outcome(OWNER_ID, body.title, week_start=body.week_start)
    outcome["related_priorities"] = []
    return outcome


@router.patch("/week/outcomes/{outcome_id}", response_model=WeeklyOutcomeOut)
def update_weekly_outcome(outcome_id: int, body: OutcomeStatusIn):
    row = execution.update_weekly_outcome_status(
        OWNER_ID, outcome_id, body.status, carried_forward=body.carried_forward
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Weekly outcome not found")
    with_related = dict(row)
    for outcome in execution.get_weekly_outcomes(OWNER_ID):
        if outcome["id"] == outcome_id:
            with_related["related_priorities"] = outcome["related_priorities"]
            break
    else:
        with_related["related_priorities"] = []
    return with_related


@router.post("/week/review", response_model=WeeklyReviewOut)
def submit_weekly_review(body: WeeklyReviewIn):
    return execution.submit_weekly_review(
        OWNER_ID,
        week_start=body.week_start,
        went_well=body.went_well,
        failed_follow_through=body.failed_follow_through,
        reason=body.reason,
        pattern_observed=body.pattern_observed,
        next_week_adjustment=body.next_week_adjustment,
    )


@router.get("/week/review-suggestions", response_model=WeeklyReviewSuggestionsOut)
def get_weekly_review_suggestions(week_start: Optional[str] = None):
    """Pre-fills for the weekly review wizard — chips seeded from what
    actually happened this week, all editable/overridable, never auto-applied."""
    return execution.get_weekly_review_suggestions(OWNER_ID, week_start)


# ---------------------------------------------------------------------------
# Development cycle / progress / mentorship
# ---------------------------------------------------------------------------


@router.get("/cycle/active", response_model=Optional[CycleOut])
def get_active_cycle():
    return execution.get_active_cycle(OWNER_ID)


@router.post("/cycle", response_model=CycleOut)
def create_cycle(body: CycleIn):
    return execution.create_cycle(
        OWNER_ID, body.title, body.start_date, body.end_date, development_action=body.development_action
    )


@router.get("/progress", response_model=ProgressOut)
def get_progress(cycle_id: Optional[int] = None):
    return {"weeks": execution.get_progress(OWNER_ID, cycle_id)}


@router.get("/mentor-summary", response_model=MentorSummaryOut)
def get_mentor_summary(cycle_id: Optional[int] = None):
    return {"summary": execution.generate_mentor_summary(OWNER_ID, cycle_id)}


@router.get("/mentorship-context", response_model=MentorshipContextOut)
def get_mentorship_context():
    return execution.get_mentorship_context(OWNER_ID)


@router.post("/mentorship-context", response_model=MentorshipContextOut)
def set_mentorship_context(body: MentorshipContextIn):
    return execution.set_mentorship_context(OWNER_ID, **body.model_dump())


# ---------------------------------------------------------------------------
# Life page — read-only glance widgets. No write paths here: money, mood,
# people, and system are all owned by chat/voice input already; this page
# only visualizes what's already in the database.
# ---------------------------------------------------------------------------


@router.get("/money", response_model=MoneyOut)
def get_money():
    budget = expenses.get_budget_summary(OWNER_ID)
    ticker = finance.get_price_ticker()
    return {
        "month_total": budget["total"],
        "month_count": budget["count"],
        "budget": budget["budget"],
        "remaining": budget["remaining"],
        "pct_used": budget["pct_used"],
        "by_category": budget["by_category"],
        "gold": ticker["gold"],
        "ter": ticker["ter"],
    }


@router.get("/system", response_model=SystemOut)
def get_system(namespace: Optional[str] = None):
    return devops.k8s_health_summary(namespace)


@router.get("/mood", response_model=MoodOut)
def get_mood():
    return {
        "week_trend": habits.weekly_mood_entries(OWNER_ID),
        "habits": habits.habit_streaks(OWNER_ID),
    }


@router.get("/people", response_model=PeopleGlanceOut)
def get_people_glance():
    return {
        "upcoming_birthdays": people.upcoming_birthdays(OWNER_ID),
        "no_contact": people.stale_contacts(OWNER_ID),
    }
