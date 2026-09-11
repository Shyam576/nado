"""
api/models.py — Pydantic request/response models for the dashboard API.

Dates are plain 'YYYY-MM-DD' strings throughout (validated by pattern, not
parsed into datetime.date) — this matches modules/execution.py, which
treats dates as opaque ISO strings passed straight through to SQL, not
datetime objects. Keeping the same representation at every layer avoids a
timezone-conversion step that has nothing to convert (these are calendar
days, not instants).
"""

from typing import Optional

from pydantic import BaseModel, Field

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_TIME_PATTERN = r"^\d{2}:\d{2}$"


# ---------------------------------------------------------------------------
# Today — requests
# ---------------------------------------------------------------------------


class MorningPlanIn(BaseModel):
    date: Optional[str] = Field(None, pattern=_DATE_PATTERN)
    target_start_time: Optional[str] = Field(None, pattern=_TIME_PATTERN)
    must_not_slip: Optional[str] = None


class PriorityIn(BaseModel):
    date: Optional[str] = Field(None, pattern=_DATE_PATTERN)
    title: str = Field(min_length=1)
    weekly_outcome_id: Optional[int] = None


class PriorityEditIn(BaseModel):
    title: str = Field(min_length=1)


class ReorderIn(BaseModel):
    date: str = Field(pattern=_DATE_PATTERN)
    ordered_priority_ids: list[int]


class CarryForwardIn(BaseModel):
    reason: Optional[str] = None
    to_date: Optional[str] = Field(None, pattern=_DATE_PATTERN)


class EveningReviewIn(BaseModel):
    date: Optional[str] = Field(None, pattern=_DATE_PATTERN)
    actual_start_time: Optional[str] = Field(None, pattern=_TIME_PATTERN)
    punctual: Optional[bool] = None
    worked_by_priority: Optional[bool] = None
    execution_score: Optional[int] = Field(None, ge=1, le=10)
    adjustment_for_tomorrow: Optional[str] = None


# ---------------------------------------------------------------------------
# Today — responses
# ---------------------------------------------------------------------------


class PriorityOut(BaseModel):
    id: int
    daily_plan_id: int
    title: str
    priority_order: int
    status: str
    completed_at: Optional[str] = None
    carried_forward: int
    carry_forward_reason: Optional[str] = None
    weekly_outcome_id: Optional[int] = None
    created_at: str


class DailyPlanOut(BaseModel):
    id: int
    chat_id: str
    date: str
    target_start_time: Optional[str] = None
    actual_start_time: Optional[str] = None
    punctual: Optional[int] = None
    must_not_slip: Optional[str] = None
    worked_by_priority: Optional[int] = None
    execution_score: Optional[int] = None
    adjustment_for_tomorrow: Optional[str] = None
    review_completed: int
    created_at: str


class TodayOut(BaseModel):
    plan: DailyPlanOut
    priorities: list[PriorityOut]


class TaskOut(BaseModel):
    id: int
    title: str


class CalendarEventOut(BaseModel):
    time: str
    summary: str


class TodayContextOut(BaseModel):
    pending_tasks: list[TaskOut]
    calendar_events: Optional[list[CalendarEventOut]] = None


class ReviewSuggestionsOut(BaseModel):
    suggested_actual_start_time: Optional[str] = None
    suggested_punctual: Optional[bool] = None
    suggested_score: Optional[int] = None
    suggested_adjustment: str
    carry_forward_reasons: list[str]


# ---------------------------------------------------------------------------
# Week — requests
# ---------------------------------------------------------------------------


class WeeklyOutcomeIn(BaseModel):
    week_start: Optional[str] = Field(None, pattern=_DATE_PATTERN)
    title: str = Field(min_length=1)


class OutcomeStatusIn(BaseModel):
    status: str = Field(pattern=r"^(pending|in_progress|done|dropped)$")
    carried_forward: Optional[bool] = None


class WeeklyReviewIn(BaseModel):
    week_start: Optional[str] = Field(None, pattern=_DATE_PATTERN)
    went_well: Optional[str] = None
    failed_follow_through: Optional[str] = None
    reason: Optional[str] = None
    pattern_observed: Optional[str] = None
    next_week_adjustment: Optional[str] = None


class IncompleteOutcomeOut(BaseModel):
    id: int
    title: str


class IncompletePriorityOut(BaseModel):
    id: int
    title: str


class IncompleteItemsOut(BaseModel):
    outcomes: list[IncompleteOutcomeOut]
    priorities: list[IncompletePriorityOut]


class ReasonCountOut(BaseModel):
    reason: str
    count: int


class WeeklyReviewSuggestionsOut(BaseModel):
    incomplete_items: IncompleteItemsOut
    carry_forward_reasons: list[ReasonCountOut]
    common_adjustment: Optional[str] = None


class CycleIn(BaseModel):
    title: str = Field(min_length=1)
    start_date: str = Field(pattern=_DATE_PATTERN)
    end_date: str = Field(pattern=_DATE_PATTERN)
    development_action: Optional[str] = None


# ---------------------------------------------------------------------------
# Week — responses
# ---------------------------------------------------------------------------


class RelatedPriorityOut(BaseModel):
    id: int
    title: str
    status: str
    carried_forward: int


class WeeklyOutcomeOut(BaseModel):
    id: int
    weekly_plan_id: int
    title: str
    status: str
    completed_at: Optional[str] = None
    carried_forward: int
    outcome_order: int
    created_at: str
    related_priorities: list[RelatedPriorityOut]


class WeekMetricsOut(BaseModel):
    week_start: str
    week_end: str
    punctuality_pct: Optional[float] = None
    completion_pct: Optional[float] = None
    carry_forward_count: int
    review_consistency_pct: Optional[float] = None
    reviews_completed: int
    days_elapsed: int
    weekly_review_done: bool
    avg_execution_score: Optional[float] = None


class WeeklyReviewOut(BaseModel):
    id: int
    chat_id: str
    week_start: str
    went_well: Optional[str] = None
    failed_follow_through: Optional[str] = None
    reason: Optional[str] = None
    pattern_observed: Optional[str] = None
    next_week_adjustment: Optional[str] = None
    punctuality_pct: Optional[float] = None
    completion_pct: Optional[float] = None
    carry_forward_count: Optional[int] = None
    review_consistency_pct: Optional[float] = None
    created_at: str


class WeekOut(BaseModel):
    week_start: str
    week_end: str
    outcomes: list[WeeklyOutcomeOut]
    scorecard: WeekMetricsOut
    review: Optional[WeeklyReviewOut] = None


# ---------------------------------------------------------------------------
# Progress / mentorship — responses
# ---------------------------------------------------------------------------


class CycleOut(BaseModel):
    id: int
    chat_id: str
    title: str
    start_date: str
    end_date: str
    development_action: Optional[str] = None
    status: str
    created_at: str


class ProgressOut(BaseModel):
    weeks: list[WeekMetricsOut]


class MentorSummaryOut(BaseModel):
    summary: str


# ---------------------------------------------------------------------------
# Header strip / Life page (read-only glance widgets)
# ---------------------------------------------------------------------------


class NextReminderOut(BaseModel):
    message: str
    fire_at: str


class HeaderOut(BaseModel):
    date: str
    must_not_slip: Optional[str] = None
    next_reminder: Optional[NextReminderOut] = None
    k8s_healthy: bool
    k8s_unhealthy_count: int


class MentorshipContextOut(BaseModel):
    development_action: Optional[str] = None
    what: Optional[str] = None
    why: Optional[str] = None
    when: Optional[str] = None
    who: Optional[str] = None
    where: Optional[str] = None
    how: Optional[str] = None
    how_much: Optional[str] = None


class SpendCategoryOut(BaseModel):
    category: str
    total: float


class GoldPriceOut(BaseModel):
    price: float
    change_24h_pct: Optional[float] = None


class TerCurrencyOut(BaseModel):
    ask: float
    bid: float
    change_24h_pct: Optional[float] = None


class TerTickerOut(BaseModel):
    usd: Optional[TerCurrencyOut] = None
    inr: Optional[TerCurrencyOut] = None
    btn: Optional[TerCurrencyOut] = None


class MoneyOut(BaseModel):
    month_total: float
    month_count: int
    budget: Optional[float] = None
    remaining: Optional[float] = None
    pct_used: Optional[float] = None
    by_category: list[SpendCategoryOut]
    gold: Optional[GoldPriceOut] = None
    ter: TerTickerOut


class SystemDeploymentOut(BaseModel):
    name: str
    status: str
    detail: Optional[str] = None


class SystemOut(BaseModel):
    namespace: str
    healthy: bool
    checked: bool
    unhealthy_count: int
    deployments: list[SystemDeploymentOut]


class MoodEntryOut(BaseModel):
    mood: str
    energy: Optional[int] = None
    created_at: str


class HabitStreakOut(BaseModel):
    habit: str
    streak: int
    days_this_week: int


class MoodOut(BaseModel):
    week_trend: list[MoodEntryOut]
    habits: list[HabitStreakOut]


class UpcomingBirthdayOut(BaseModel):
    name: str
    birthday: str
    days_away: int


class StaleContactOut(BaseModel):
    name: str
    days_since_contact: int


class PeopleGlanceOut(BaseModel):
    upcoming_birthdays: list[UpcomingBirthdayOut]
    no_contact: list[StaleContactOut]


class MentorshipContextIn(BaseModel):
    what: Optional[str] = None
    why: Optional[str] = None
    when: Optional[str] = None
    who: Optional[str] = None
    where: Optional[str] = None
    how: Optional[str] = None
    how_much: Optional[str] = None
