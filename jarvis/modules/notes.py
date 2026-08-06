"""
modules/notes.py — zero-friction thought capture.

A place to just say something without needing structure — logged as-is,
searchable via modules/recall.py, and fed into the weekly review's LLM
reflection alongside tasks/mood/expenses/habits, so scattered thoughts turn
into something instead of just disappearing into chat history.

Distinct from modules/communication.process_meeting_notes ("/notes"), which
summarizes a block of meeting notes and auto-creates tasks from it — this
is the opposite: no summarization, no structure, just remember it verbatim.
"""

import datetime
import logging
from typing import Optional

from store.db import get_connection

logger = logging.getLogger(__name__)


def add_note(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Log a freeform note, verbatim.

    Args:
        chat_id: The owner this note belongs to.
        args: The note text, as separate words to be joined.

    Returns:
        A short confirmation, or a usage message if no text was given.
    """
    args = args or []
    text = " ".join(args).strip()
    if not text:
        return "Usage: /note <text>"

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO notes (chat_id, text, created_at) VALUES (?, ?, ?)",
            (chat_id, text, datetime.datetime.now().isoformat()),
        )
    return "Noted."


def list_notes(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """List recent notes, most recent first.

    Args:
        chat_id: The owner to list notes for.
        args: Optional [N] to control how many entries to show (default 10).

    Returns:
        A newline-separated list, or a message if there are none.
    """
    args = args or []
    limit = 10
    if args and args[0].isdigit():
        limit = int(args[0])

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT text, created_at FROM notes WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()

    if not rows:
        return "No notes yet. Just tell me something and I'll remember it — try /note <text>."

    lines = []
    for row in rows:
        when = datetime.datetime.fromisoformat(row["created_at"]).strftime("%b %-d, %-I:%M %p")
        lines.append(f"{when} — {row['text']}")
    return "\n".join(lines)


def weekly_notes(chat_id: str) -> list[str]:
    """Return note texts from the last 7 days, oldest first — for the weekly review.

    Args:
        chat_id: The owner to look up.

    Returns:
        A list of note text strings.
    """
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT text FROM notes WHERE chat_id = ? AND created_at >= ? ORDER BY created_at",
            (chat_id, week_ago),
        ).fetchall()
    return [row["text"] for row in rows]
