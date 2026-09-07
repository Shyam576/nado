"""
api/routes.py — JSON API for the web dashboard.

Thin HTTP wrapper around modules/execution.py — every handler here does
request validation (via api/models.py) and response shaping, nothing else.
No business logic lives in this file; it belongs in modules/execution.py so
the bot transports (bot/commands.py, modules/intent.py) and the dashboard
call the exact same functions against the exact same tables. See
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
    MentorshipContextIn,
    MentorshipContextOut,
    MentorSummaryOut,
    MorningPlanIn,
    OutcomeStatusIn,
    PriorityEditIn,
    PriorityIn,
    PriorityOut,
    ProgressOut,
    ReorderIn,
    TodayOut,
    WeekOut,
    WeeklyOutcomeIn,
    WeeklyOutcomeOut,
    WeeklyReviewIn,
    WeeklyReviewOut,
)
from config import OWNER_ID
from modules import execution

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


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
