"""
modules/nudges.py — State-based proactive nudges.

Unlike the time-based check-ins in proactive.py (voice mode only), these fire
because something in the user's data warrants a message: budget crossing a
threshold, a day with no expenses logged, tasks going stale. Each function
returns the message to send (or None / empty list), and the job-queue
callbacks in bot/telegram_bot.py deliver via notifier.broadcast() — the same
pattern as reminders and price alerts.

De-duplication state lives in memory.py preferences so a nudge fires once per
trigger, not on every poll.
"""

import calendar
import datetime
import logging

import memory
from store.db import get_connection

logger = logging.getLogger(__name__)

# Budget thresholds (fraction of monthly budget) that each fire one alert per month
BUDGET_ALERT_THRESHOLDS = (0.8, 1.0)

# A pending task older than this is considered stale
STALE_TASK_DAYS = 5


def budget_pacing_alert(chat_id: str) -> str | None:
    """Return an alert when month-to-date spend crosses a budget threshold.

    Fires once per threshold per calendar month (state in memory preferences),
    so it can be polled frequently without spamming.

    Args:
        chat_id: The owner whose expenses are totalled.

    Returns:
        The alert message, or None if no new threshold was crossed.
    """
    budget = memory.get_preference("monthly_budget")
    if not budget:
        return None
    budget = float(budget)

    today = datetime.date.today()
    month_start = today.replace(day=1).isoformat()
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE chat_id = ? AND date(created_at) >= ? AND amount IS NOT NULL",
            (chat_id, month_start),
        ).fetchone()[0]

    state_key = f"budget_alerts_fired_{today.strftime('%Y-%m')}"
    fired: list = memory.get_preference(state_key, [])

    for threshold in BUDGET_ALERT_THRESHOLDS:
        if total >= budget * threshold and threshold not in fired:
            fired.append(threshold)
            memory.set_preference(state_key, fired)
            pct = int(threshold * 100)
            if threshold >= 1.0:
                days_left = calendar.monthrange(today.year, today.month)[1] - today.day
                return (
                    f"Budget alert: you've hit your monthly budget — "
                    f"{total:,.2f} of {budget:,.2f} BTN spent with "
                    f"{days_left} days left in {today.strftime('%B')}."
                )
            return (
                f"Heads up: you're at {pct}% of your monthly budget "
                f"({total:,.2f} of {budget:,.2f} BTN) and it's only the "
                f"{today.day}{'th' if 11 <= today.day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(today.day % 10, 'th')}."
            )
    return None


def evening_expense_nudge(chat_id: str) -> str | None:
    """Return a gentle nudge if no expense has been logged today.

    Intended to run once per evening via a daily job.

    Args:
        chat_id: The owner whose expenses are checked.

    Returns:
        The nudge message, or None if something was already logged today.
    """
    today = datetime.date.today().isoformat()
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE chat_id = ? AND date(created_at) = ?",
            (chat_id, today),
        ).fetchone()[0]

    if count > 0:
        return None
    return "No expenses logged today — nothing spent, or just forgot? A quick “spent 100 on …” keeps the budget honest."


def stale_task_nudge(chat_id: str) -> str | None:
    """Return a one-time nudge listing pending tasks untouched for 5+ days.

    Each task is nudged at most once (IDs tracked in memory preferences), so
    this stays a reminder, not a nag.

    Args:
        chat_id: The owner whose tasks are checked.

    Returns:
        The nudge message, or None if there are no newly-stale tasks.
    """
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=STALE_TASK_DAYS)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at FROM tasks "
            "WHERE chat_id = ? AND status = 'pending' AND created_at <= ? ORDER BY created_at",
            (chat_id, cutoff),
        ).fetchall()

    already_nudged: list = memory.get_preference("stale_tasks_nudged", [])
    fresh = [row for row in rows if row["id"] not in already_nudged]
    if not fresh:
        return None

    memory.set_preference("stale_tasks_nudged", already_nudged + [row["id"] for row in fresh])

    lines = [f"These tasks have been sitting for {STALE_TASK_DAYS}+ days:"]
    for row in fresh:
        age_days = (datetime.datetime.now() - datetime.datetime.fromisoformat(row["created_at"])).days
        lines.append(f"  #{row['id']} {row['title']} ({age_days} days)")
    lines.append("Still relevant? Say “mark task <id> as done”, or just tell me to drop them.")
    return "\n".join(lines)
