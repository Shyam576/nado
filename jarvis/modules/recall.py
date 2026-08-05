"""
modules/recall.py — keyword search across past tasks/expenses/mood/habit history.

A lightweight "what did I log about X" search, not a vector-embedding RAG
pipeline: plain SQL LIKE queries across the existing structured stores.
Simpler, deterministic, and needs no new dependency (no embedding model, no
vector index) for a personal dataset that will only ever be a few thousand
rows — the same reasoning modules/tasks.py's find_pending_tasks_matching
already uses, just extended across every store instead of just tasks.
"""

import datetime
import logging
from typing import Optional

from store.db import get_connection

logger = logging.getLogger(__name__)

_MAX_RESULTS_PER_CATEGORY = 5


def _short_date(iso_str: str) -> str:
    """Format an ISO timestamp as e.g. 'Aug 5', falling back to the raw string on failure."""
    try:
        return datetime.datetime.fromisoformat(iso_str).strftime("%b %-d")
    except ValueError:
        return iso_str


def _search_tasks(chat_id: str, keyword: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, status, created_at FROM tasks "
            "WHERE chat_id = ? AND title LIKE ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, f"%{keyword}%", _MAX_RESULTS_PER_CATEGORY),
        ).fetchall()
    return [
        f"  #{row['id']} {row['title']} ({row['status']}, {_short_date(row['created_at'])})"
        for row in rows
    ]


def _search_expenses(chat_id: str, keyword: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, amount, remarks, recipient, category, created_at FROM expenses "
            "WHERE chat_id = ? AND (remarks LIKE ? OR recipient LIKE ? OR category LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (chat_id, f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", _MAX_RESULTS_PER_CATEGORY),
        ).fetchall()

    lines = []
    for row in rows:
        amount = f"{row['amount']:,.2f} BTN" if row["amount"] is not None else "unknown amount"
        detail = row["remarks"] or row["recipient"] or row["category"] or ""
        lines.append(f"  #{row['id']} {amount} — {detail} ({_short_date(row['created_at'])})")
    return lines


def _search_mood(chat_id: str, keyword: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT mood, energy, note, created_at FROM mood_log "
            "WHERE chat_id = ? AND (mood LIKE ? OR note LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (chat_id, f"%{keyword}%", f"%{keyword}%", _MAX_RESULTS_PER_CATEGORY),
        ).fetchall()

    lines = []
    for row in rows:
        line = f"  {row['mood']}"
        if row["energy"] is not None:
            line += f" ({row['energy']}/10)"
        if row["note"]:
            line += f" — {row['note']}"
        line += f" ({_short_date(row['created_at'])})"
        lines.append(line)
    return lines


def _search_habits(chat_id: str, keyword: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT habit, completed_at FROM habit_log "
            "WHERE chat_id = ? AND habit LIKE ? ORDER BY completed_at DESC LIMIT ?",
            (chat_id, f"%{keyword}%", _MAX_RESULTS_PER_CATEGORY),
        ).fetchall()
    return [f"  {row['habit']} — {_short_date(row['completed_at'])}" for row in rows]


def recall(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Search tasks/expenses/mood/habit history for a keyword.

    Args:
        chat_id: The owner whose history to search.
        args: The search keyword(s) — joined into one phrase and matched as
              a substring (case-insensitive) against each store's text
              fields.

    Returns:
        Up to 5 matches per category, grouped by category, or a "nothing
        found" message. A usage message if no keyword was given.
    """
    args = args or []
    keyword = " ".join(args).strip()
    if not keyword:
        return "Usage: /recall <keyword>"

    sections = [
        ("Tasks", _search_tasks(chat_id, keyword)),
        ("Expenses", _search_expenses(chat_id, keyword)),
        ("Mood", _search_mood(chat_id, keyword)),
        ("Habits", _search_habits(chat_id, keyword)),
    ]

    lines = [f"Results for “{keyword}”:"]
    found_any = False
    for label, matches in sections:
        if matches:
            found_any = True
            lines.append(f"\n{label}:")
            lines.extend(matches)

    if not found_any:
        return f"Nothing found matching “{keyword}”."

    return "\n".join(lines)
