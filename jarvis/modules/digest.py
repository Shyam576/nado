"""
modules/digest.py — Daily digest combining tasks, habits, and price movement.

Assembles a single proactive "good morning" style message from data that
already exists in other modules — no new external calls beyond what
finance.py already makes. Delivered by a scheduled job in bot/telegram_bot.py
and also available on-demand via /digest.
"""

import datetime
import logging

from modules import expenses, finance, habits, tasks

logger = logging.getLogger(__name__)

_WEEKLY_REVIEW_SYSTEM = (
    "You are Jarvis's weekly-review brain. Given the user's last 7 days of tasks "
    "completed, mood entries, habit adherence, and expenses by category, write a short "
    "(3-5 sentence) reflective summary. Point out genuine patterns you can see in the data "
    "(e.g. spending spikes in a category, mood trends, low task throughput, a habit lapse "
    "coinciding with a mood dip) — don't just restate the numbers. If the data is too sparse "
    "to say anything meaningful, say so plainly rather than inventing a pattern. Plain text "
    "only, no markdown."
)


def build_daily_digest(chat_id: str) -> str:
    """Assemble the daily digest text for one chat.

    Args:
        chat_id: The chat to build the digest for (scopes tasks/habits;
                 prices are global).

    Returns:
        A multi-section plain-text digest.
    """
    today = datetime.date.today().strftime("%A, %B %-d")
    sections = [f"Good morning — {today}", ""]

    sections.append("Tasks:")
    sections.append(tasks.today_summary(chat_id))
    sections.append("")

    habit_summary = habits.habit_status(chat_id)
    sections.append("Habits:")
    sections.append(habit_summary)
    sections.append("")

    sections.append("Markets:")
    sections.append(finance.price_change_summary())

    return "\n".join(sections)


def build_weekly_review(chat_id: str = "", args: list[str] | None = None) -> str:
    """Assemble a reflective weekly review — LLM reasoning over the week's data,
    not just a raw numbers dump (that's what /digest already does daily).

    Args:
        chat_id: The chat to build the review for.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A short reflective summary, or an error message if the LLM call failed.
    """
    task_stats = tasks.weekly_stats(chat_id)
    mood_entries = habits.weekly_mood_entries(chat_id)
    habit_adherence = habits.weekly_habit_adherence(chat_id)
    expense_stats = expenses.weekly_expense_summary(chat_id)

    context_lines = [
        f"Tasks completed this week: {task_stats['completed_count']}",
        f"Tasks still pending: {task_stats['pending_count']}",
    ]
    if task_stats["completed_titles"]:
        context_lines.append("Completed: " + "; ".join(task_stats["completed_titles"]))

    if mood_entries:
        mood_line = ", ".join(
            f"{e['mood']}" + (f" ({e['energy']}/10)" if e["energy"] is not None else "")
            for e in mood_entries
        )
        context_lines.append(f"Mood entries this week: {mood_line}")
    else:
        context_lines.append("No mood entries logged this week.")

    if habit_adherence:
        habit_line = ", ".join(f"{h['habit']}: {h['days_logged']}/7 days" for h in habit_adherence)
        context_lines.append(f"Habit adherence this week: {habit_line}")
    else:
        context_lines.append("No habits tracked this week.")

    if expense_stats["total"]:
        cat_line = ", ".join(f"{k}: {v:,.2f}" for k, v in expense_stats["by_category"].items())
        context_lines.append(f"Expenses this week ({expense_stats['total']:,.2f} BTN total): {cat_line}")
    else:
        context_lines.append("No expenses logged this week.")

    context = "\n".join(context_lines)

    import brain  # local import — avoids loading the LLM at module import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _WEEKLY_REVIEW_SYSTEM},
                {"role": "user", "content": context},
            ],
            max_tokens=300,
            temperature=0.6,
        )
        review = response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Weekly review LLM call failed: %s", exc)
        return "Something went wrong generating the weekly review — try again shortly."

    return f"Weekly Review\n\n{context}\n\n{review}"
