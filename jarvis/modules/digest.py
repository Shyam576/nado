"""
modules/digest.py — Daily digest combining tasks, habits, and price movement.

Assembles a single proactive "good morning" style message from data that
already exists in other modules — no new external calls beyond what
finance.py already makes. Delivered by a scheduled job in bot/telegram_bot.py
and also available on-demand via /digest.
"""

import datetime
import logging

from modules import finance, habits, tasks

logger = logging.getLogger(__name__)


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
