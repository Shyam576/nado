"""
modules/execution.py — Execution-discipline tracking: daily plans, evening
reviews, weekly outcomes, weekly reviews, and the metrics/mentor summary
built from them.

This is the ONLY place this business logic lives. The Telegram/Discord bot
(via bot/commands.py + modules/intent.py) and the web dashboard (via api/)
both call these same functions against the same store/db.py tables — see
store/db.py's "Execution-discipline tracking" schema block. Neither
transport is allowed to read/write these tables directly; that would create
a second source of truth.

Dates are plain ISO 'YYYY-MM-DD' strings (local calendar days, not
timestamps) — daily_plans/weekly_plans are keyed on them directly, matching
how habit_log already uses date(completed_at) for calendar-day grouping.
Weeks run Monday-Sunday, identified by their Monday (week_start).
"""

import datetime
import logging
from typing import Optional

import memory
from store.db import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _today() -> str:
    return datetime.date.today().isoformat()


def week_start_for(date_str: str) -> str:
    """Return the Monday (ISO date) of the week containing `date_str`."""
    d = datetime.date.fromisoformat(date_str)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def _week_end_for(week_start: str) -> str:
    return (datetime.date.fromisoformat(week_start) + datetime.timedelta(days=6)).isoformat()


# ---------------------------------------------------------------------------
# Daily plan — morning
# ---------------------------------------------------------------------------


def get_or_create_daily_plan(chat_id: str, date: Optional[str] = None) -> dict:
    """Return the daily_plans row for `date` (default today), creating an empty one if needed."""
    date = date or _today()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM daily_plans WHERE chat_id = ? AND date = ?", (chat_id, date)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO daily_plans (chat_id, date, created_at) VALUES (?, ?, ?)",
                (chat_id, date, datetime.datetime.now().isoformat()),
            )
            row = conn.execute(
                "SELECT * FROM daily_plans WHERE chat_id = ? AND date = ?", (chat_id, date)
            ).fetchone()
    return dict(row)


def set_morning_plan(
    chat_id: str,
    date: Optional[str] = None,
    target_start_time: Optional[str] = None,
    must_not_slip: Optional[str] = None,
) -> dict:
    """Set/update today's target start time and non-negotiable commitment.

    Args:
        chat_id: The owner this plan belongs to.
        date: ISO date, defaults to today.
        target_start_time: 'HH:MM', or None to leave unchanged.
        must_not_slip: The one commitment that must not slip, or None to leave unchanged.

    Returns:
        The updated daily_plans row as a dict.
    """
    plan = get_or_create_daily_plan(chat_id, date)
    date = plan["date"]

    with get_connection() as conn:
        if target_start_time is not None:
            conn.execute(
                "UPDATE daily_plans SET target_start_time = ? WHERE id = ?",
                (target_start_time, plan["id"]),
            )
        if must_not_slip is not None:
            conn.execute(
                "UPDATE daily_plans SET must_not_slip = ? WHERE id = ?", (must_not_slip, plan["id"])
            )
    return get_or_create_daily_plan(chat_id, date)


def add_priority(
    chat_id: str, title: str, date: Optional[str] = None, weekly_outcome_id: Optional[int] = None
) -> dict:
    """Add a priority to today's (or `date`'s) plan, appended after existing priorities.

    Args:
        chat_id: The owner this plan belongs to.
        title: The priority's text.
        date: ISO date, defaults to today.
        weekly_outcome_id: Optional link to the weekly outcome this priority supports.

    Returns:
        The newly created daily_priorities row as a dict.
    """
    plan = get_or_create_daily_plan(chat_id, date)
    with get_connection() as conn:
        next_order = conn.execute(
            "SELECT COALESCE(MAX(priority_order), -1) + 1 FROM daily_priorities WHERE daily_plan_id = ?",
            (plan["id"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO daily_priorities "
            "(daily_plan_id, title, priority_order, weekly_outcome_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (plan["id"], title, next_order, weekly_outcome_id, datetime.datetime.now().isoformat()),
        )
        row = conn.execute(
            "SELECT * FROM daily_priorities WHERE daily_plan_id = ? AND priority_order = ?",
            (plan["id"], next_order),
        ).fetchone()
    return dict(row)


def add_priorities(chat_id: str, titles: list[str], date: Optional[str] = None) -> list[dict]:
    """Add several priorities at once, in order — e.g. from a "my top priorities are: 1...2...3..."
    chat message (see modules/intent.py's set_daily_priorities intent). Always additive, same as
    add_priority(): existing priorities for the day are kept, never replaced or cleared.
    """
    return [add_priority(chat_id, title, date=date) for title in titles if title.strip()]


def edit_priority(chat_id: str, priority_id: int, title: str) -> Optional[dict]:
    """Rename a priority. Returns the updated row, or None if it doesn't belong to `chat_id`."""
    if not _priority_belongs_to(chat_id, priority_id):
        return None
    with get_connection() as conn:
        conn.execute("UPDATE daily_priorities SET title = ? WHERE id = ?", (title, priority_id))
        row = conn.execute("SELECT * FROM daily_priorities WHERE id = ?", (priority_id,)).fetchone()
    return dict(row)


def reorder_priorities(chat_id: str, date: str, ordered_priority_ids: list[int]) -> list[dict]:
    """Set priority_order to match `ordered_priority_ids`'s sequence for the given day's plan.

    Args:
        chat_id: The owner this plan belongs to.
        date: ISO date of the plan being reordered.
        ordered_priority_ids: Priority IDs in their new display order.

    Returns:
        The plan's priorities in their new order.
    """
    plan = get_or_create_daily_plan(chat_id, date)
    with get_connection() as conn:
        valid_ids = {
            row["id"]
            for row in conn.execute(
                "SELECT id FROM daily_priorities WHERE daily_plan_id = ?", (plan["id"],)
            ).fetchall()
        }
        for order, priority_id in enumerate(ordered_priority_ids):
            if priority_id in valid_ids:
                conn.execute(
                    "UPDATE daily_priorities SET priority_order = ? WHERE id = ?", (order, priority_id)
                )
    return get_priorities(chat_id, date)


def complete_priority(chat_id: str, priority_id: int) -> Optional[dict]:
    """Mark a priority done. Returns the updated row, or None if it doesn't belong to `chat_id`."""
    if not _priority_belongs_to(chat_id, priority_id):
        return None
    with get_connection() as conn:
        conn.execute(
            "UPDATE daily_priorities SET status = 'done', completed_at = ? WHERE id = ?",
            (datetime.datetime.now().isoformat(), priority_id),
        )
        row = conn.execute("SELECT * FROM daily_priorities WHERE id = ?", (priority_id,)).fetchone()
    return dict(row)


def uncomplete_priority(chat_id: str, priority_id: int) -> Optional[dict]:
    """Undo a completion (e.g. an accidental checkbox click) — back to 'pending'.

    Returns the updated row, or None if it doesn't belong to `chat_id`.
    """
    if not _priority_belongs_to(chat_id, priority_id):
        return None
    with get_connection() as conn:
        conn.execute(
            "UPDATE daily_priorities SET status = 'pending', completed_at = NULL WHERE id = ?",
            (priority_id,),
        )
        row = conn.execute("SELECT * FROM daily_priorities WHERE id = ?", (priority_id,)).fetchone()
    return dict(row)


def carry_forward_priority(
    chat_id: str, priority_id: int, reason: Optional[str] = None, to_date: Optional[str] = None
) -> Optional[dict]:
    """Mark a priority carried-forward and re-create it on a later day's plan.

    Args:
        chat_id: The owner this plan belongs to.
        priority_id: The priority not completed today.
        reason: Why it slipped — shown in the evening review and weekly rollup.
        to_date: ISO date to carry it to, defaults to tomorrow relative to its own plan's date.

    Returns:
        The newly created priority row on the target day, or None if `priority_id`
        doesn't belong to `chat_id`.
    """
    if not _priority_belongs_to(chat_id, priority_id):
        return None

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM daily_priorities WHERE id = ?", (priority_id,)).fetchone()
        plan = conn.execute("SELECT * FROM daily_plans WHERE id = ?", (row["daily_plan_id"],)).fetchone()

    if to_date is None:
        to_date = (datetime.date.fromisoformat(plan["date"]) + datetime.timedelta(days=1)).isoformat()

    with get_connection() as conn:
        conn.execute(
            "UPDATE daily_priorities SET status = 'carried_forward', carried_forward = 1, "
            "carry_forward_reason = ? WHERE id = ?",
            (reason, priority_id),
        )

    return add_priority(chat_id, row["title"], date=to_date, weekly_outcome_id=row["weekly_outcome_id"])


def get_priorities(chat_id: str, date: Optional[str] = None) -> list[dict]:
    """Return a day's priorities in display order (empty list if no plan exists yet)."""
    date = date or _today()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT p.* FROM daily_priorities p JOIN daily_plans d ON p.daily_plan_id = d.id "
            "WHERE d.chat_id = ? AND d.date = ? ORDER BY p.priority_order",
            (chat_id, date),
        ).fetchall()
    return [dict(row) for row in rows]


def _priority_belongs_to(chat_id: str, priority_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM daily_priorities p JOIN daily_plans d ON p.daily_plan_id = d.id "
            "WHERE p.id = ? AND d.chat_id = ?",
            (priority_id, chat_id),
        ).fetchone()
    return row is not None


def get_today(chat_id: str, date: Optional[str] = None) -> dict:
    """Return the full Today view: plan fields + ordered priorities.

    Args:
        chat_id: The owner to look up.
        date: ISO date, defaults to today.

    Returns:
        {"plan": {...daily_plans row...}, "priorities": [...daily_priorities rows...]}
    """
    date = date or _today()
    plan = get_or_create_daily_plan(chat_id, date)
    return {"plan": plan, "priorities": get_priorities(chat_id, date)}


# ---------------------------------------------------------------------------
# Daily plan — evening review
# ---------------------------------------------------------------------------


def submit_evening_review(
    chat_id: str,
    date: Optional[str] = None,
    actual_start_time: Optional[str] = None,
    punctual: Optional[bool] = None,
    worked_by_priority: Optional[bool] = None,
    execution_score: Optional[int] = None,
    adjustment_for_tomorrow: Optional[str] = None,
) -> dict:
    """Record the evening review for a day's plan and mark it reviewed.

    Individual priority outcomes (completed/carried-forward) are recorded
    separately via complete_priority()/carry_forward_priority() — this call
    captures only the plan-level reflection fields.

    Args:
        chat_id: The owner this plan belongs to.
        date: ISO date, defaults to today.
        actual_start_time: 'HH:MM' actual start/arrival time.
        punctual: Whether the target start time was met.
        worked_by_priority: True if the day was driven by the plan, False if mostly reactive.
        execution_score: 1-10 self-rating.
        adjustment_for_tomorrow: One change to make tomorrow.

    Returns:
        The updated daily_plans row as a dict.

    Raises:
        ValueError: If execution_score is provided but not in 1-10.
    """
    if execution_score is not None and not (1 <= execution_score <= 10):
        raise ValueError("execution_score must be between 1 and 10")

    plan = get_or_create_daily_plan(chat_id, date)
    with get_connection() as conn:
        conn.execute(
            "UPDATE daily_plans SET "
            "actual_start_time = COALESCE(?, actual_start_time), "
            "punctual = COALESCE(?, punctual), "
            "worked_by_priority = COALESCE(?, worked_by_priority), "
            "execution_score = COALESCE(?, execution_score), "
            "adjustment_for_tomorrow = COALESCE(?, adjustment_for_tomorrow), "
            "review_completed = 1 "
            "WHERE id = ?",
            (
                actual_start_time,
                None if punctual is None else int(punctual),
                None if worked_by_priority is None else int(worked_by_priority),
                execution_score,
                adjustment_for_tomorrow,
                plan["id"],
            ),
        )
    return get_or_create_daily_plan(chat_id, plan["date"])


# ---------------------------------------------------------------------------
# Evening review wizard — suggestions
#
# Every value here is a proposed default the wizard shows as editable and
# overridable, never auto-applied — the dashboard's own write path
# (submit_evening_review above) is unchanged and doesn't call any of this.
# ---------------------------------------------------------------------------

# Tappable carry-forward reasons — a starting taxonomy, not a fixed
# enum enforced anywhere: carry_forward_reason is a free TEXT column, so an
# "Other" free-text entry is always possible alongside these chips.
CARRY_FORWARD_REASONS = [
    "Ran out of time",
    "Blocked by someone else",
    "Lower priority than expected",
    "Still in progress",
    "No longer relevant",
]

_ADJUSTMENT_SUGGESTION_SYSTEM = (
    "You are Jarvis's evening-review assistant. Given today's punctuality and which "
    "priorities were carried forward (and why), draft ONE short, concrete sentence "
    "the user can accept as-is or edit — a specific adjustment for tomorrow. Not "
    "generic advice like 'be more productive' — tie it to what actually happened "
    "today. Plain text, one sentence, no markdown."
)


def suggest_execution_score(
    chat_id: str, date: Optional[str] = None, punctual: Optional[bool] = None
) -> Optional[int]:
    """Suggest a 1-10 execution score from today's completion rate + punctuality.

    A starting heuristic (70% weight on priorities completed, a 30%-weight
    bonus for punctuality), not a measured truth — the wizard always shows
    this as a draggable slider pre-set to the suggestion, never auto-applied.

    Args:
        chat_id: The owner to compute for.
        date: ISO date, defaults to today.
        punctual: Pre-computed punctuality for the day (e.g. from comparing
            target vs. a suggested actual start time). Not read from
            daily_plans.punctual — at suggestion time the review hasn't been
            submitted yet, so that column is still null.

    Returns:
        An integer 1-10, or None if there's nothing to base a suggestion on
        (no priorities logged and punctuality unknown).
    """
    priorities = get_priorities(chat_id, date or _today())
    completion_pct = (
        100.0 * sum(1 for p in priorities if p["status"] == "done") / len(priorities) if priorities else None
    )

    if completion_pct is None and punctual is None:
        return None

    score = (completion_pct / 100.0) * 7 if completion_pct is not None else 0
    score += 3 if punctual else 0
    return max(1, min(10, round(score)))


def suggest_adjustment(chat_id: str, date: Optional[str] = None) -> str:
    """Draft a one-line "adjustment for tomorrow" via a one-shot LLM call.

    Grounded in today's actual carry-forward reasons and punctuality — same
    fresh-call pattern as generate_mentor_summary() (doesn't touch
    brain._history). The wizard shows the result as an editable draft.

    Args:
        chat_id: The owner to draft for.
        date: ISO date, defaults to today.

    Returns:
        A one-sentence suggestion, or a plain fallback if there's not enough
        data yet or the LLM call fails.
    """
    import brain  # local import — avoids loading the model at module import time

    date = date or _today()
    plan = get_or_create_daily_plan(chat_id, date)
    priorities = get_priorities(chat_id, date)

    if not priorities:
        return "Set at least one priority tomorrow morning to build momentum."

    carried = [p for p in priorities if p["status"] == "carried_forward"]
    context_lines = [
        f"Punctual: {'yes' if plan['punctual'] else 'no' if plan['punctual'] is not None else 'unknown'}"
    ]
    if carried:
        context_lines.append("Carried forward:")
        for p in carried:
            reason = f" — {p['carry_forward_reason']}" if p["carry_forward_reason"] else ""
            context_lines.append(f"  {p['title']}{reason}")
    else:
        context_lines.append("Nothing carried forward.")

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _ADJUSTMENT_SUGGESTION_SYSTEM},
                {"role": "user", "content": "\n".join(context_lines)},
            ],
            max_tokens=60,
            temperature=0.5,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.error("Adjustment suggestion generation failed: %s", exc)
        return "Reflect on today and note one thing to change tomorrow."


def get_review_suggestions(chat_id: str, date: Optional[str] = None) -> dict:
    """Bundle every evening-review wizard pre-fill into one call.

    Args:
        chat_id: The owner to compute suggestions for.
        date: ISO date, defaults to today.

    Returns:
        {
          "suggested_actual_start_time": "HH:MM" | None,  # from activity.first_sample_time
          "suggested_punctual": bool | None,               # target vs. suggested actual time
          "suggested_score": int | None,
          "suggested_adjustment": str,
        }
        suggested_punctual is None (never guessed) if either time is
        missing/malformed — the wizard just asks directly in that case.
    """
    from modules import activity  # local import — avoids a module-load cycle at import time

    date = date or _today()
    plan = get_or_create_daily_plan(chat_id, date)

    suggested_start = activity.first_sample_time(chat_id, date)
    suggested_punctual = None
    if suggested_start and plan["target_start_time"]:
        try:
            target = datetime.datetime.strptime(plan["target_start_time"], "%H:%M")
            actual = datetime.datetime.strptime(suggested_start, "%H:%M")
            suggested_punctual = actual <= target
        except ValueError:
            suggested_punctual = None  # malformed stored time — don't guess, just ask

    return {
        "suggested_actual_start_time": suggested_start,
        "suggested_punctual": suggested_punctual,
        "suggested_score": suggest_execution_score(chat_id, date, punctual=suggested_punctual),
        "suggested_adjustment": suggest_adjustment(chat_id, date),
    }


# ---------------------------------------------------------------------------
# Weekly plan / outcomes
# ---------------------------------------------------------------------------


def get_or_create_weekly_plan(chat_id: str, week_start: Optional[str] = None) -> dict:
    """Return the weekly_plans row for the week containing `week_start` (default this week)."""
    week_start = week_start_for(week_start or _today())
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM weekly_plans WHERE chat_id = ? AND week_start = ?", (chat_id, week_start)
        ).fetchone()
        if row is None:
            active_cycle = get_active_cycle(chat_id)
            conn.execute(
                "INSERT INTO weekly_plans (chat_id, cycle_id, week_start, week_end, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    chat_id,
                    active_cycle["id"] if active_cycle else None,
                    week_start,
                    _week_end_for(week_start),
                    datetime.datetime.now().isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM weekly_plans WHERE chat_id = ? AND week_start = ?",
                (chat_id, week_start),
            ).fetchone()
    return dict(row)


def add_weekly_outcome(chat_id: str, title: str, week_start: Optional[str] = None) -> dict:
    """Add one outcome (of the 3-5 for the week) to a weekly plan, creating the plan if needed."""
    plan = get_or_create_weekly_plan(chat_id, week_start)
    with get_connection() as conn:
        next_order = conn.execute(
            "SELECT COALESCE(MAX(outcome_order), -1) + 1 FROM weekly_outcomes WHERE weekly_plan_id = ?",
            (plan["id"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO weekly_outcomes (weekly_plan_id, title, outcome_order, created_at) "
            "VALUES (?, ?, ?, ?)",
            (plan["id"], title, next_order, datetime.datetime.now().isoformat()),
        )
        row = conn.execute(
            "SELECT * FROM weekly_outcomes WHERE weekly_plan_id = ? AND outcome_order = ?",
            (plan["id"], next_order),
        ).fetchone()
    return dict(row)


def _outcome_belongs_to(chat_id: str, outcome_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM weekly_outcomes o JOIN weekly_plans p ON o.weekly_plan_id = p.id "
            "WHERE o.id = ? AND p.chat_id = ?",
            (outcome_id, chat_id),
        ).fetchone()
    return row is not None


def update_weekly_outcome_status(
    chat_id: str, outcome_id: int, status: str, carried_forward: Optional[bool] = None
) -> Optional[dict]:
    """Update a weekly outcome's status (and completed_at if status is 'done').

    Args:
        chat_id: The owner this outcome belongs to.
        outcome_id: The outcome to update.
        status: 'pending' | 'in_progress' | 'done' | 'dropped'.
        carried_forward: Whether this outcome is being pushed to next week, if changing.

    Returns:
        The updated row, or None if `outcome_id` doesn't belong to `chat_id`.
    """
    if not _outcome_belongs_to(chat_id, outcome_id):
        return None
    completed_at = datetime.datetime.now().isoformat() if status == "done" else None
    with get_connection() as conn:
        if carried_forward is None:
            conn.execute(
                "UPDATE weekly_outcomes SET status = ?, completed_at = ? WHERE id = ?",
                (status, completed_at, outcome_id),
            )
        else:
            conn.execute(
                "UPDATE weekly_outcomes SET status = ?, completed_at = ?, carried_forward = ? WHERE id = ?",
                (status, completed_at, int(carried_forward), outcome_id),
            )
        row = conn.execute("SELECT * FROM weekly_outcomes WHERE id = ?", (outcome_id,)).fetchone()
    return dict(row)


def get_weekly_outcomes(chat_id: str, week_start: Optional[str] = None) -> list[dict]:
    """Return a week's outcomes in display order, each with its linked daily priorities."""
    week_start = week_start_for(week_start or _today())
    with get_connection() as conn:
        outcomes = conn.execute(
            "SELECT o.* FROM weekly_outcomes o JOIN weekly_plans p ON o.weekly_plan_id = p.id "
            "WHERE p.chat_id = ? AND p.week_start = ? ORDER BY o.outcome_order",
            (chat_id, week_start),
        ).fetchall()
        result = []
        for outcome in outcomes:
            linked = conn.execute(
                "SELECT id, title, status, carried_forward FROM daily_priorities "
                "WHERE weekly_outcome_id = ? ORDER BY created_at",
                (outcome["id"],),
            ).fetchall()
            entry = dict(outcome)
            entry["related_priorities"] = [dict(row) for row in linked]
            result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def verdict_for_pct(pct: Optional[float]) -> Optional[str]:
    """Map a percentage metric to a short glance verdict instead of a raw number.

    Starting thresholds (80/50), easy to tune — used by glance views (the
    Week page's scorecard); Progress's trend charts keep exact numbers,
    never verdicts, per the "percentages for trends only" rule.

    Args:
        pct: A percentage value (0-100), or None if not yet computable.

    Returns:
        "On track" / "Slipping" / "Off track", or None if `pct` is None.
    """
    if pct is None:
        return None
    if pct >= 80:
        return "On track"
    if pct >= 50:
        return "Slipping"
    return "Off track"


def compute_week_metrics(chat_id: str, week_start: Optional[str] = None) -> dict:
    """Compute this week's scorecard live from daily_plans/daily_priorities.

    Args:
        chat_id: The owner to compute metrics for.
        week_start: Any ISO date in the target week, defaults to this week.

    Returns:
        {
          "punctuality_pct": float | None,   # punctual days / days with punctuality recorded
          "completion_pct": float | None,    # done priorities / total priorities planned
          "carry_forward_count": int,
          "review_consistency_pct": float | None,  # reviews done / days elapsed so far this week
          "reviews_completed": int,
          "days_elapsed": int,
          "weekly_review_done": bool,
          "avg_execution_score": float | None,   # mean of this week's 1-10 daily scores
        }
    """
    week_start = week_start_for(week_start or _today())
    week_end = _week_end_for(week_start)

    with get_connection() as conn:
        plans = conn.execute(
            "SELECT * FROM daily_plans WHERE chat_id = ? AND date >= ? AND date <= ?",
            (chat_id, week_start, week_end),
        ).fetchall()

        plan_ids = [p["id"] for p in plans]
        priorities = []
        if plan_ids:
            placeholders = ",".join("?" * len(plan_ids))
            priorities = conn.execute(
                f"SELECT * FROM daily_priorities WHERE daily_plan_id IN ({placeholders})", plan_ids
            ).fetchall()

        review_done = conn.execute(
            "SELECT 1 FROM weekly_reviews WHERE chat_id = ? AND week_start = ?", (chat_id, week_start)
        ).fetchone()

    punctuality_days = [p for p in plans if p["punctual"] is not None]
    punctuality_pct = (
        100.0 * sum(1 for p in punctuality_days if p["punctual"]) / len(punctuality_days)
        if punctuality_days
        else None
    )

    completion_pct = (
        100.0 * sum(1 for pr in priorities if pr["status"] == "done") / len(priorities)
        if priorities
        else None
    )

    carry_forward_count = sum(1 for pr in priorities if pr["carried_forward"])

    today = datetime.date.today().isoformat()
    days_elapsed = (
        (datetime.date.fromisoformat(min(today, week_end)) - datetime.date.fromisoformat(week_start)).days
        + 1
        if today >= week_start
        else 0
    )
    reviews_completed = sum(1 for p in plans if p["review_completed"])
    review_consistency_pct = 100.0 * reviews_completed / days_elapsed if days_elapsed else None

    scores = [p["execution_score"] for p in plans if p["execution_score"] is not None]
    avg_execution_score = sum(scores) / len(scores) if scores else None

    return {
        "week_start": week_start,
        "week_end": week_end,
        "punctuality_pct": punctuality_pct,
        "completion_pct": completion_pct,
        "carry_forward_count": carry_forward_count,
        "review_consistency_pct": review_consistency_pct,
        "reviews_completed": reviews_completed,
        "days_elapsed": days_elapsed,
        "weekly_review_done": review_done is not None,
        "avg_execution_score": avg_execution_score,
    }


def submit_weekly_review(
    chat_id: str,
    week_start: Optional[str] = None,
    went_well: Optional[str] = None,
    failed_follow_through: Optional[str] = None,
    reason: Optional[str] = None,
    pattern_observed: Optional[str] = None,
    next_week_adjustment: Optional[str] = None,
) -> dict:
    """Save the weekly review, snapshotting this week's metrics alongside it.

    Args:
        chat_id: The owner this review belongs to.
        week_start: Any ISO date in the target week, defaults to this week.
        went_well: Reflection answers — see store/db.py's weekly_reviews columns.
        failed_follow_through: See above.
        reason: See above.
        pattern_observed: See above.
        next_week_adjustment: See above.

    Returns:
        The saved weekly_reviews row as a dict, metrics included.
    """
    week_start = week_start_for(week_start or _today())
    metrics = compute_week_metrics(chat_id, week_start)

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM weekly_reviews WHERE chat_id = ? AND week_start = ?", (chat_id, week_start)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO weekly_reviews (chat_id, week_start, created_at) VALUES (?, ?, ?)",
                (chat_id, week_start, datetime.datetime.now().isoformat()),
            )
        # Metrics are always overwritten with the freshest snapshot; reflection
        # fields use COALESCE so re-submitting one question doesn't blank the
        # others — same partial-update convention as submit_evening_review().
        conn.execute(
            "UPDATE weekly_reviews SET "
            "went_well = COALESCE(?, went_well), "
            "failed_follow_through = COALESCE(?, failed_follow_through), "
            "reason = COALESCE(?, reason), "
            "pattern_observed = COALESCE(?, pattern_observed), "
            "next_week_adjustment = COALESCE(?, next_week_adjustment), "
            "punctuality_pct = ?, completion_pct = ?, carry_forward_count = ?, review_consistency_pct = ? "
            "WHERE chat_id = ? AND week_start = ?",
            (
                went_well,
                failed_follow_through,
                reason,
                pattern_observed,
                next_week_adjustment,
                metrics["punctuality_pct"],
                metrics["completion_pct"],
                metrics["carry_forward_count"],
                metrics["review_consistency_pct"],
                chat_id,
                week_start,
            ),
        )
        row = conn.execute(
            "SELECT * FROM weekly_reviews WHERE chat_id = ? AND week_start = ?", (chat_id, week_start)
        ).fetchone()
    return dict(row)


def get_weekly_review(chat_id: str, week_start: Optional[str] = None) -> Optional[dict]:
    """Return the saved weekly review for a week, or None if not yet submitted."""
    week_start = week_start_for(week_start or _today())
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM weekly_reviews WHERE chat_id = ? AND week_start = ?", (chat_id, week_start)
        ).fetchone()
    return dict(row) if row else None


def get_week(chat_id: str, week_start: Optional[str] = None) -> dict:
    """Return the full Week view: outcomes, scorecard, and this week's saved review (if any)."""
    week_start = week_start_for(week_start or _today())
    return {
        "week_start": week_start,
        "week_end": _week_end_for(week_start),
        "outcomes": get_weekly_outcomes(chat_id, week_start),
        "scorecard": compute_week_metrics(chat_id, week_start),
        "review": get_weekly_review(chat_id, week_start),
    }


# ---------------------------------------------------------------------------
# Weekly review wizard — suggestions (chips seeded from what actually
# happened this week, not a fixed generic list where one exists).
# ---------------------------------------------------------------------------


def weekly_incomplete_items(chat_id: str, week_start: Optional[str] = None) -> dict:
    """Return this week's carried-forward/dropped items — chips for the weekly
    review's "what did I fail to follow through on" step.

    Args:
        chat_id: The owner to look up.
        week_start: Any ISO date in the target week, defaults to this week.

    Returns:
        {
          "outcomes": [{"id": int, "title": str}, ...],    # dropped, or carried_forward=1
          "priorities": [{"id": int, "title": str}, ...],  # carried_forward this week
        }
    """
    week_start = week_start_for(week_start or _today())
    week_end = _week_end_for(week_start)

    with get_connection() as conn:
        outcome_rows = conn.execute(
            "SELECT o.id, o.title FROM weekly_outcomes o JOIN weekly_plans p ON o.weekly_plan_id = p.id "
            "WHERE p.chat_id = ? AND p.week_start = ? AND (o.status = 'dropped' OR o.carried_forward = 1) "
            "ORDER BY o.outcome_order",
            (chat_id, week_start),
        ).fetchall()

        priority_rows = conn.execute(
            "SELECT pr.id, pr.title FROM daily_priorities pr JOIN daily_plans d ON pr.daily_plan_id = d.id "
            "WHERE d.chat_id = ? AND d.date >= ? AND d.date <= ? AND pr.status = 'carried_forward' "
            "ORDER BY pr.created_at",
            (chat_id, week_start, week_end),
        ).fetchall()

    return {
        "outcomes": [dict(r) for r in outcome_rows],
        "priorities": [dict(r) for r in priority_rows],
    }


def weekly_carry_forward_reasons(chat_id: str, week_start: Optional[str] = None) -> list[dict]:
    """Return this week's carry-forward reasons, most common first.

    Chips for the weekly review's "why" step, seeded from what actually
    happened this week rather than always showing the same generic list.

    Args:
        chat_id: The owner to look up.
        week_start: Any ISO date in the target week, defaults to this week.

    Returns:
        A list of {"reason": str, "count": int} dicts, most frequent first.
        Falls back to CARRY_FORWARD_REASONS (count=0 each) if nothing was
        recorded this week, so the step always has something to tap.
    """
    week_start = week_start_for(week_start or _today())
    week_end = _week_end_for(week_start)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT pr.carry_forward_reason AS reason, COUNT(*) AS n "
            "FROM daily_priorities pr JOIN daily_plans d ON pr.daily_plan_id = d.id "
            "WHERE d.chat_id = ? AND d.date >= ? AND d.date <= ? "
            "AND pr.carry_forward_reason IS NOT NULL "
            "GROUP BY pr.carry_forward_reason ORDER BY n DESC",
            (chat_id, week_start, week_end),
        ).fetchall()

    if not rows:
        return [{"reason": reason, "count": 0} for reason in CARRY_FORWARD_REASONS]
    return [{"reason": row["reason"], "count": row["n"]} for row in rows]


def weekly_common_adjustment(chat_id: str, week_start: Optional[str] = None) -> Optional[str]:
    """Return this week's most-repeated daily "adjustment for tomorrow", if any repeated.

    A chip suggestion for the weekly review's final step — only offered if
    the same adjustment came up 2+ times, so a single one-off isn't
    presented as if it were a pattern.

    Args:
        chat_id: The owner to look up.
        week_start: Any ISO date in the target week, defaults to this week.

    Returns:
        The most-repeated adjustment text, or None if nothing repeated.
    """
    week_start = week_start_for(week_start or _today())
    week_end = _week_end_for(week_start)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT adjustment_for_tomorrow AS adjustment, COUNT(*) AS n FROM daily_plans "
            "WHERE chat_id = ? AND date >= ? AND date <= ? AND adjustment_for_tomorrow IS NOT NULL "
            "GROUP BY adjustment_for_tomorrow ORDER BY n DESC LIMIT 1",
            (chat_id, week_start, week_end),
        ).fetchone()

    if row and row["n"] >= 2:
        return row["adjustment"]
    return None


def get_weekly_review_suggestions(chat_id: str, week_start: Optional[str] = None) -> dict:
    """Bundle every weekly-review wizard pre-fill into one call.

    Args:
        chat_id: The owner to compute suggestions for.
        week_start: Any ISO date in the target week, defaults to this week.

    Returns:
        {
          "incomplete_items": {"outcomes": [...], "priorities": [...]},
          "carry_forward_reasons": [{"reason": str, "count": int}, ...],
          "common_adjustment": str | None,
        }
    """
    week_start = week_start_for(week_start or _today())
    return {
        "incomplete_items": weekly_incomplete_items(chat_id, week_start),
        "carry_forward_reasons": weekly_carry_forward_reasons(chat_id, week_start),
        "common_adjustment": weekly_common_adjustment(chat_id, week_start),
    }


# ---------------------------------------------------------------------------
# Development cycle / progress
# ---------------------------------------------------------------------------


def create_cycle(
    chat_id: str, title: str, start_date: str, end_date: str, development_action: Optional[str] = None
) -> dict:
    """Start a new 8-12 week development cycle (marks any other active cycle as 'completed')."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE development_cycles SET status = 'completed' WHERE chat_id = ? AND status = 'active'",
            (chat_id,),
        )
        conn.execute(
            "INSERT INTO development_cycles "
            "(chat_id, title, start_date, end_date, development_action, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (chat_id, title, start_date, end_date, development_action, datetime.datetime.now().isoformat()),
        )
        row = conn.execute(
            "SELECT * FROM development_cycles WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)
        ).fetchone()
    return dict(row)


def get_active_cycle(chat_id: str) -> Optional[dict]:
    """Return the current active development cycle, or None if none has been started."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM development_cycles WHERE chat_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
    return dict(row) if row else None


def get_progress(chat_id: str, cycle_id: Optional[int] = None) -> list[dict]:
    """Return week-by-week metrics across a development cycle, oldest week first.

    Args:
        chat_id: The owner to compute progress for.
        cycle_id: Cycle to report on, defaults to the active cycle.

    Returns:
        A list of compute_week_metrics()-shaped dicts, one per week from the
        cycle's start_date through min(today, end_date). Empty list if no
        cycle is active and none was specified.
    """
    cycle = None
    if cycle_id is not None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM development_cycles WHERE id = ? AND chat_id = ?", (cycle_id, chat_id)
            ).fetchone()
        cycle = dict(row) if row else None
    else:
        cycle = get_active_cycle(chat_id)

    if cycle is None:
        return []

    today = datetime.date.today().isoformat()
    last_week_start = week_start_for(min(today, cycle["end_date"]))

    weeks = []
    cursor = week_start_for(cycle["start_date"])
    while cursor <= last_week_start:
        weeks.append(compute_week_metrics(chat_id, cursor))
        cursor = (datetime.date.fromisoformat(cursor) + datetime.timedelta(days=7)).isoformat()
    return weeks


# ---------------------------------------------------------------------------
# Mentor summary
# ---------------------------------------------------------------------------

_MENTOR_SUMMARY_SYSTEM = (
    "You are Jarvis's mentorship-prep brain. Given this week's and last week's execution "
    "metrics (punctuality %, priority completion %, carry-forward count, daily review "
    "consistency %, average mood energy, most-logged mood, and spending), plus the user's "
    "own weekly-review reflections, write a concise mentor session summary in exactly this "
    "shape:\n\n"
    "Since the previous mentorship session:\n"
    "• <metric change bullets, only for metrics that actually changed or are notable>\n\n"
    "Main pattern noticed:\n<1-2 sentences, grounded in the data and reflections given, not "
    "invented — mood or spending only belongs here if it plausibly connects to execution, "
    "e.g. a mood dip coinciding with a punctuality slip; don't force a connection that isn't "
    "there>\n\n"
    "Adjustment for next cycle:\n<1 sentence, concrete and actionable>\n\n"
    "If there isn't enough data yet (e.g. no prior week to compare against), say so plainly "
    "instead of inventing numbers or a pattern. Plain text only, no markdown."
)


def _week_mood_and_money(chat_id: str, week_start: str, week_end: str) -> dict:
    """Return mood/spend context for a specific week — internal helper for
    generate_mentor_summary(), scoped to week_start/week_end.

    Deliberately NOT habits.weekly_mood_entries()/expenses.weekly_expense_summary()
    — those are relative to "the last 7 days from right now," which would
    misrepresent a historical week (e.g. last_week in the mentor summary's
    week-over-week comparison isn't necessarily the most recent 7 days).

    Returns:
        {
          "avg_energy": float | None,     # mean of logged 1-10 energy this week
          "top_mood": str | None,          # most-logged mood word this week
          "total_spend": float,            # 0.0 if nothing logged
          "top_category": str | None,      # highest-spend category this week
        }
    """
    with get_connection() as conn:
        mood_rows = conn.execute(
            "SELECT energy, mood FROM mood_log "
            "WHERE chat_id = ? AND date(created_at) >= ? AND date(created_at) <= ?",
            (chat_id, week_start, week_end),
        ).fetchall()
        expense_rows = conn.execute(
            "SELECT COALESCE(category, 'Uncategorised') AS category, SUM(amount) AS total FROM expenses "
            "WHERE chat_id = ? AND date(created_at) >= ? AND date(created_at) <= ? AND amount IS NOT NULL "
            "GROUP BY category ORDER BY total DESC",
            (chat_id, week_start, week_end),
        ).fetchall()

    energies = [row["energy"] for row in mood_rows if row["energy"] is not None]
    avg_energy = sum(energies) / len(energies) if energies else None

    mood_counts: dict[str, int] = {}
    for row in mood_rows:
        mood_counts[row["mood"]] = mood_counts.get(row["mood"], 0) + 1
    top_mood = max(mood_counts, key=mood_counts.get) if mood_counts else None

    return {
        "avg_energy": avg_energy,
        "top_mood": top_mood,
        "total_spend": sum(row["total"] for row in expense_rows),
        "top_category": expense_rows[0]["category"] if expense_rows else None,
    }


def generate_mentor_summary(chat_id: str, cycle_id: Optional[int] = None) -> str:
    """Generate the mentor-session summary via a one-shot LLM call, grounded in real metrics.

    Compares the most recently completed week against the week before it, plus
    both weeks' weekly-review reflections if present. Uses a fresh one-shot
    call (doesn't touch brain._history), same pattern as digest.py's weekly
    review and decision.py's decide().

    Args:
        chat_id: The owner to summarise.
        cycle_id: Cycle to report on, defaults to the active cycle.

    Returns:
        The mentor summary text, or a plain message if there isn't yet a
        full week of data to compare.
    """
    import brain  # local import — avoids loading the model at module import time

    progress = get_progress(chat_id, cycle_id)
    if len(progress) < 1:
        return "Not enough data yet for a mentor summary — complete at least one week first."

    this_week = progress[-1]
    last_week = progress[-2] if len(progress) >= 2 else None
    this_review = get_weekly_review(chat_id, this_week["week_start"])
    last_review = get_weekly_review(chat_id, last_week["week_start"]) if last_week else None

    def _fmt(week, review):
        if week is None:
            return "  (no prior week)"
        mood_money = _week_mood_and_money(chat_id, week["week_start"], week["week_end"])
        lines = [
            f"  Punctuality: {week['punctuality_pct']:.0f}%" if week["punctuality_pct"] is not None else "  Punctuality: n/a",
            f"  Priority completion: {week['completion_pct']:.0f}%" if week["completion_pct"] is not None else "  Priority completion: n/a",
            f"  Carry-forward: {week['carry_forward_count']}",
            f"  Review consistency: {week['review_consistency_pct']:.0f}%" if week["review_consistency_pct"] is not None else "  Review consistency: n/a",
            f"  Avg energy: {mood_money['avg_energy']:.1f}/10" if mood_money["avg_energy"] is not None else "  Avg energy: no mood entries logged",
        ]
        if mood_money["top_mood"]:
            lines.append(f"  Most logged mood: {mood_money['top_mood']}")
        if mood_money["total_spend"]:
            category_note = f" (top category: {mood_money['top_category']})" if mood_money["top_category"] else ""
            lines.append(f"  Spend: {mood_money['total_spend']:,.2f} BTN{category_note}")
        else:
            lines.append("  Spend: none logged")
        if review:
            if review["pattern_observed"]:
                lines.append(f"  Pattern noted: {review['pattern_observed']}")
            if review["next_week_adjustment"]:
                lines.append(f"  Adjustment made: {review['next_week_adjustment']}")
        return "\n".join(lines)

    context = (
        f"Last week ({last_week['week_start'] if last_week else 'n/a'}):\n{_fmt(last_week, last_review)}\n\n"
        f"This week ({this_week['week_start']}):\n{_fmt(this_week, this_review)}"
    )

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _MENTOR_SUMMARY_SYSTEM},
                {"role": "user", "content": context},
            ],
            max_tokens=300,
            temperature=0.4,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.error("Mentor summary generation failed: %s", exc)
        return "Couldn't generate the mentor summary right now — try again shortly."


# ---------------------------------------------------------------------------
# Chat-text formatting — shared by bot/commands.py's /plan, /review,
# /weekstatus and modules/intent.py's conversational equivalents, so the
# Telegram/Discord phrasing and the dashboard read the exact same data.
# ---------------------------------------------------------------------------


def describe_today(chat_id: str) -> str:
    """Render today's plan (target time, must-not-slip, priorities) as chat text."""
    today = get_today(chat_id)
    plan, priorities = today["plan"], today["priorities"]

    lines = [f"Today ({plan['date']}):"]
    if plan["target_start_time"]:
        lines.append(f"Target start: {plan['target_start_time']}")
    if plan["must_not_slip"]:
        lines.append(f"Must not slip: {plan['must_not_slip']}")

    if not priorities:
        lines.append("")
        lines.append(
            "No priorities set yet. Tell me your top priorities, e.g. "
            '"My top priorities today are: 1. Fix deployment 2. Review PR 3. Send client response".'
        )
    else:
        lines.append("")
        lines.append("Priorities:")
        for i, p in enumerate(priorities, start=1):
            mark = "✓" if p["status"] == "done" else "→" if p["status"] == "carried_forward" else "•"
            lines.append(f"  {mark} {i}. {p['title']}")

    return "\n".join(lines)


def review_prompt(chat_id: str) -> str:
    """Show today's priority status and prompt for a natural-language evening review.

    Triggered by "review my day" with no details — the reply guides the user
    toward a free-text answer that the submit_daily_review intent then parses
    (see modules/intent.py). If the review is already done, shows it instead
    of prompting again — same "don't re-ask" rule as the dashboard's Today page.
    """
    today = get_today(chat_id)
    plan, priorities = today["plan"], today["priorities"]

    if plan["review_completed"]:
        return "Today's review is already done:\n\n" + describe_today(chat_id)

    lines = ["Here's where today stands:"]
    if priorities:
        for i, p in enumerate(priorities, start=1):
            mark = "✓" if p["status"] == "done" else "→" if p["status"] == "carried_forward" else "•"
            lines.append(f"  {mark} {i}. {p['title']}")
    else:
        lines.append("  (no priorities were set today)")
    lines.append("")
    lines.append(
        "Tell me how it went, e.g. \"I was punctual, worked by priority, "
        "execution score 7, adjustment: start 15 minutes earlier\"."
    )
    return "\n".join(lines)


def describe_week(chat_id: str) -> str:
    """Render this week's scorecard + outcomes as chat text — answers "how did I do this week"."""
    week = get_week(chat_id)
    s = week["scorecard"]

    def _pct(v):
        return f"{v:.0f}%" if v is not None else "n/a"

    lines = [f"Week of {week['week_start']} – {week['week_end']}:"]
    lines.append(f"  Punctuality: {_pct(s['punctuality_pct'])}")
    lines.append(f"  Priority completion: {_pct(s['completion_pct'])}")
    lines.append(f"  Carried-forward items: {s['carry_forward_count']}")
    lines.append(f"  Daily reviews: {s['reviews_completed']}/{s['days_elapsed']}")
    lines.append(f"  Weekly review: {'done' if s['weekly_review_done'] else 'not done yet'}")

    if week["outcomes"]:
        lines.append("")
        lines.append("Outcomes:")
        for o in week["outcomes"]:
            mark = (
                "✓" if o["status"] == "done"
                else "✗" if o["status"] == "dropped"
                else "→" if o["carried_forward"]
                else "•"
            )
            lines.append(f"  {mark} {o['title']} ({o['status'].replace('_', ' ')})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mentorship-prep context (5Ws + 2Hs)
# ---------------------------------------------------------------------------

_5W2H_FIELDS = ("what", "why", "when", "who", "where", "how", "how_much")


def get_mentorship_context(chat_id: str) -> dict:
    """Return the mentorship page's context: development action + 5Ws/2Hs.

    development_action comes from the active cycle (set at cycle creation).
    The 5Ws/2Hs are freeform text the user fills in once and rarely changes —
    they live in memory.py's preferences store rather than a new DB table,
    matching memory.py's own documented scope ("preferences / facts").
    """
    cycle = get_active_cycle(chat_id)
    saved = memory.get_preference("mentorship_5w2h", {})
    return {
        "development_action": cycle["development_action"] if cycle else None,
        **{field: saved.get(field) for field in _5W2H_FIELDS},
    }


def set_mentorship_context(chat_id: str, **fields) -> dict:
    """Update one or more 5Ws/2Hs fields — a field left as None keeps its saved value."""
    saved = memory.get_preference("mentorship_5w2h", {})
    for field in _5W2H_FIELDS:
        if fields.get(field) is not None:
            saved[field] = fields[field]
    memory.set_preference("mentorship_5w2h", saved)
    return get_mentorship_context(chat_id)
