"""
modules/captures.py — zero-friction, typed thought capture ("KoraOS" inbox).

Distinct from modules/tasks.py (a flat pending/done list) and modules/notes.py
(untyped freeform text): a capture is classified into one of CAPTURE_TYPES,
optionally tagged with a project and a scheduled date, and carries its own
status lifecycle (CAPTURE_STATUSES) so it can be triaged later — turned into
a daily priority, archived, or corrected if the classification was wrong.

Classification pipeline (see AGENTS.md's "deterministic rules first, LLM for
ambiguous cases" guidance): the LLM is the primary classifier whenever it's
available. When it isn't (this bot currently runs with no local LLM on
production — see requirements-server.txt), a small set of high-precision
keyword/phrase rules (_FALLBACK_TYPE_RULES) catches the unambiguous cases
("remind me...", "idea for...", "investigate...") instead of leaving
everything as 'unknown' — same reasoning as modules/expenses.py's
_keyword_classify_category fallback. It's deliberately narrow: a capture
that matches nothing still saves as 'unknown' rather than risk a wrong
guess with false confidence. classification_model records which path
produced the result (the configured LLM, 'deterministic-fallback', or None
for a plain 'unknown') so a systematically wrong guess can be traced back
to its source. Idempotency (source_message_id) is enforced independently
of all this, in add_capture() itself.

The original text and the classification are stored separately (raw_text is
never overwritten) so a wrong AI guess can always be corrected without
losing what was actually said — see correct_capture().
"""

import datetime
import json
import logging
import re
from typing import Optional

from config import OLLAMA_MODEL
from store.db import ALLOWED_CAPTURE_TRANSITIONS, get_connection

logger = logging.getLogger(__name__)

CAPTURE_TYPES: list[str] = [
    "task",
    "idea",
    "work_issue",
    "learning",
    "reminder",
    "personal_thought",
    "note",
    "unknown",
]

CAPTURE_STATUSES: list[str] = ["inbox", "planned", "in_progress", "completed", "archived"]

_PROMPT_VERSION = "captures-v1"

_CLASSIFY_SYSTEM = (
    "You classify a quickly-captured thought into a structured record. Reply with ONLY a "
    "JSON object: {\"type\": <one of the types below>, \"project\": <string or null>, "
    "\"scheduled_for\": <YYYY-MM-DD or null>, \"confidence\": <0.0-1.0>}.\n\n"
    "Types:\n"
    "  task — something actionable the user explicitly wants to DO.\n"
    "  idea — a suggestion, proposal, or \"what if\" for something to build/try/automate.\n"
    "  work_issue — a technical/work problem to investigate or fix (a bug, an incident, "
    "something broken or behaving wrong).\n"
    "  learning — something to study, learn, or read about.\n"
    "  reminder — a time-bound nudge to remember something later, often phrased "
    "\"remind me...\".\n"
    "  personal_thought — a personal reflection, feeling, or non-work observation.\n"
    "  note — a freeform observation/thought that doesn't clearly fit any other type.\n"
    "  unknown — truly ambiguous; only use this if nothing else fits.\n\n"
    "project: only set this if the text clearly names a specific project, codebase, or "
    "service (e.g. \"jarvis\", \"the dashboard\", \"nado\"); otherwise null. Never guess.\n"
    "scheduled_for: only set this if the text names or clearly implies a specific date "
    "(e.g. \"tomorrow\", \"next Monday\", an explicit date). Resolve relative dates against "
    "the given 'today' date. Never invent a date that isn't implied.\n"
    "confidence: how confident you are in the type classification specifically, 0.0-1.0.\n\n"
    "Examples:\n"
    "\"Investigate payment success without status update\" -> "
    "{\"type\": \"work_issue\", \"project\": null, \"scheduled_for\": null, \"confidence\": 0.9}\n"
    "\"Learn Kubernetes network policies\" -> "
    "{\"type\": \"learning\", \"project\": null, \"scheduled_for\": null, \"confidence\": 0.9}\n"
    "\"Idea for automating deployment reports\" -> "
    "{\"type\": \"idea\", \"project\": null, \"scheduled_for\": null, \"confidence\": 0.9}\n"
    "\"Remind me to email the API team tomorrow\" -> "
    "{\"type\": \"reminder\", \"project\": null, \"scheduled_for\": \"<tomorrow's date>\", \"confidence\": 0.9}"
)


def _now() -> str:
    return datetime.datetime.now().isoformat()


# Deterministic fallback used only when the LLM path is unavailable or fails
# — never when the LLM succeeds, even with low confidence (that's still a
# real judgment call; this is a last resort for when there's no judgment
# available at all). Checked in order; first match wins. Deliberately narrow
# and high-precision: a capture matching nothing here still saves as
# 'unknown' rather than risk a confidently-wrong guess — same trade-off as
# modules/expenses.py's _keyword_classify_category.
_FALLBACK_PROMPT_VERSION = "captures-v1-fallback"

_FALLBACK_TYPE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*remind\s+me\b", re.IGNORECASE), "reminder"),
    (re.compile(r"^\s*idea\b", re.IGNORECASE), "idea"),
    (re.compile(r"\bidea for\b", re.IGNORECASE), "idea"),
    (re.compile(r"\bwhat if\b", re.IGNORECASE), "idea"),
    (re.compile(r"^\s*(?:learn|study)\b", re.IGNORECASE), "learning"),
    (re.compile(r"^\s*investigate\b", re.IGNORECASE), "work_issue"),
    (re.compile(r"\b(?:bug|broken|not working|isn't working|doesn't work)\b", re.IGNORECASE), "work_issue"),
]


def _fallback_classify(raw_text: str) -> dict:
    """Deterministic type guess, used only when the LLM is unavailable or fails.

    Only `type` (and, for a matched 'reminder', a literal "today"/"tomorrow"
    date) is inferred — project and anything else needs real understanding a
    keyword rule can't safely guess at.

    Args:
        raw_text: The captured text to classify.

    Returns:
        Same shape as _classify()'s success return. `model` is
        'deterministic-fallback' when a rule matched, None when nothing did
        (plain 'unknown' — nothing to attribute the guess to).
    """
    for pattern, matched_type in _FALLBACK_TYPE_RULES:
        if pattern.search(raw_text):
            scheduled_for = None
            if matched_type == "reminder":
                lowered = raw_text.lower()
                today = datetime.date.today()
                if re.search(r"\btomorrow\b", lowered):
                    scheduled_for = (today + datetime.timedelta(days=1)).isoformat()
                elif re.search(r"\btoday\b", lowered):
                    scheduled_for = today.isoformat()
            return {
                "type": matched_type,
                "project": None,
                "scheduled_for": scheduled_for,
                "confidence": 0.6,  # a plain keyword match, not real judgment — kept well below a typical LLM confidence
                "model": "deterministic-fallback",
                "prompt_version": _FALLBACK_PROMPT_VERSION,
            }

    return {
        "type": "unknown",
        "project": None,
        "scheduled_for": None,
        "confidence": None,
        "model": None,
        "prompt_version": None,
    }


def _classify(raw_text: str) -> dict:
    """One-shot LLM classification. Never raises — uses _fallback_classify() on any failure.

    Args:
        raw_text: The captured text to classify.

    Returns:
        {"type": str, "project": str | None, "scheduled_for": str | None,
         "confidence": float | None, "model": str | None, "prompt_version": str | None}
    """
    import brain  # local import — avoids loading the model at module import time

    try:
        llm = brain._get_llm()
        today = datetime.date.today().isoformat()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": f"Today's date: {today}\n\nText: {raw_text}"},
            ],
            max_tokens=120,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Capture classification failed (%s) — trying deterministic fallback.", exc)
        return _fallback_classify(raw_text)

    capture_type = parsed.get("type")
    if capture_type not in CAPTURE_TYPES:
        capture_type = "unknown"

    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        confidence = None

    project = parsed.get("project")
    project = str(project).strip() if isinstance(project, str) and project.strip() else None

    scheduled_for = parsed.get("scheduled_for")
    if isinstance(scheduled_for, str):
        try:
            datetime.date.fromisoformat(scheduled_for)
        except ValueError:
            scheduled_for = None
    else:
        scheduled_for = None

    return {
        "type": capture_type,
        "project": project,
        "scheduled_for": scheduled_for,
        "confidence": confidence,
        "model": OLLAMA_MODEL,
        "prompt_version": _PROMPT_VERSION,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def add_capture(
    chat_id: str, raw_text: str, source: str, source_message_id: Optional[str] = None
) -> dict:
    """Save and classify a capture. Idempotent on (source, source_message_id).

    A retried Discord/Telegram delivery of the same message carries the same
    source_message_id — this returns the already-saved row instead of
    creating a duplicate (see store/db.py's UNIQUE(source, source_message_id)).

    Args:
        chat_id: The owner this capture belongs to.
        raw_text: The captured text, verbatim.
        source: 'telegram' | 'discord' | 'dashboard'.
        source_message_id: The originating platform message id, if any —
            required for idempotency; omit only for dashboard-originated
            captures, which have no retry-prone transport underneath them.

    Returns:
        The saved captures row as a dict (existing row if this was a retry).
    """
    if source_message_id is not None:
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT * FROM captures WHERE source = ? AND source_message_id = ?",
                (source, source_message_id),
            ).fetchone()
        if existing is not None:
            logger.info(
                "Capture retry ignored (source=%s, source_message_id=%s) — returning existing #%d",
                source, source_message_id, existing["id"],
            )
            return dict(existing)

    classification = _classify(raw_text)
    now = _now()

    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO captures (chat_id, raw_text, type, status, source, source_message_id, "
            "project, scheduled_for, classification_model, classification_prompt_version, "
            "classification_confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, 'inbox', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                raw_text,
                classification["type"],
                source,
                source_message_id,
                classification["project"],
                classification["scheduled_for"],
                classification["model"],
                classification["prompt_version"],
                classification["confidence"],
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def get_capture(chat_id: str, capture_id: int) -> Optional[dict]:
    """Return one capture, or None if it doesn't exist or isn't owned by `chat_id`."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM captures WHERE id = ? AND chat_id = ?", (capture_id, chat_id)
        ).fetchone()
    return dict(row) if row else None


def list_captures(
    chat_id: str,
    status: Optional[str] = None,
    type_: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """Return captures for `chat_id`, most recent first, optionally filtered.

    Args:
        chat_id: The owner to list captures for.
        status: Restrict to one CAPTURE_STATUSES value, or None for all.
        type_: Restrict to one CAPTURE_TYPES value, or None for all.
        project: Restrict to an exact project match, or None for all.
        limit: Maximum rows to return.

    Returns:
        A list of capture dicts.
    """
    query = "SELECT * FROM captures WHERE chat_id = ?"
    params: list = [chat_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    if type_ is not None:
        query += " AND type = ?"
        params.append(type_)
    if project is not None:
        query += " AND project = ?"
        params.append(project)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def correct_capture(
    chat_id: str,
    capture_id: int,
    type_: Optional[str] = None,
    project: Optional[str] = None,
    scheduled_for: Optional[str] = None,
) -> Optional[dict]:
    """Manually correct a capture's classification — the raw_text is never touched.

    Args:
        chat_id: The owner attempting the correction (ownership check).
        capture_id: The capture to correct.
        type_: New type (must be one of CAPTURE_TYPES), or None to leave unchanged.
        project: New project, or None to leave unchanged. Pass "" to clear it.
        scheduled_for: New ISO date, or None to leave unchanged. Pass "" to clear it.

    Returns:
        The updated row, or None if not found/not owned, `type_` is invalid,
        or `scheduled_for` is a non-empty, non-ISO-date string.
    """
    if type_ is not None and type_ not in CAPTURE_TYPES:
        return None
    if scheduled_for:
        try:
            datetime.date.fromisoformat(scheduled_for)
        except ValueError:
            return None

    capture = get_capture(chat_id, capture_id)
    if capture is None:
        return None

    new_project = capture["project"] if project is None else (project or None)
    new_scheduled_for = capture["scheduled_for"] if scheduled_for is None else (scheduled_for or None)
    new_type = capture["type"] if type_ is None else type_

    with get_connection() as conn:
        conn.execute(
            "UPDATE captures SET type = ?, project = ?, scheduled_for = ?, updated_at = ? WHERE id = ?",
            (new_type, new_project, new_scheduled_for, _now(), capture_id),
        )
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    return dict(row)


def update_status(chat_id: str, capture_id: int, status: str) -> Optional[dict]:
    """Move a capture to a new status, enforcing ALLOWED_CAPTURE_TRANSITIONS.

    Args:
        chat_id: The owner attempting the change (ownership check).
        capture_id: The capture to update.
        status: The target CAPTURE_STATUSES value.

    Returns:
        The updated row, or None if not found/not owned.

    Raises:
        ValueError: If `status` isn't a known status, or the transition from
            the capture's current status to it isn't allowed.
    """
    if status not in CAPTURE_STATUSES:
        raise ValueError(f"Unknown status: {status}")

    capture = get_capture(chat_id, capture_id)
    if capture is None:
        return None

    current = capture["status"]
    if status not in ALLOWED_CAPTURE_TRANSITIONS.get(current, set()) and status != current:
        raise ValueError(f"Cannot move capture #{capture_id} from '{current}' to '{status}'")

    with get_connection() as conn:
        conn.execute(
            "UPDATE captures SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), capture_id),
        )
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    return dict(row)


def schedule_capture(chat_id: str, capture_id: int, scheduled_for: str) -> Optional[dict]:
    """Set a capture's scheduled date directly (a thin correct_capture() wrapper).

    Args:
        chat_id: The owner attempting the change (ownership check).
        capture_id: The capture to schedule.
        scheduled_for: An ISO 'YYYY-MM-DD' date.

    Returns:
        The updated row, or None if not found/not owned, or the date is malformed.
    """
    try:
        datetime.date.fromisoformat(scheduled_for)
    except ValueError:
        return None
    return correct_capture(chat_id, capture_id, scheduled_for=scheduled_for)


# ---------------------------------------------------------------------------
# Chat-text formatting — shared by bot/commands.py's /capture and (later) the
# dashboard's Inbox page, same "one place renders this" convention as
# modules/execution.py's describe_today()/describe_week().
# ---------------------------------------------------------------------------


def describe_capture(capture: dict) -> str:
    """Render one capture as a confirmation message, including a correction hint."""
    lines = [f"Captured (#{capture['id']}): {capture['raw_text']}"]
    lines.append(f"Type: {capture['type']}")
    if capture["project"]:
        lines.append(f"Project: {capture['project']}")
    if capture["scheduled_for"]:
        lines.append(f"Scheduled: {capture['scheduled_for']}")
    if capture["classification_confidence"] is not None:
        lines.append(f"Confidence: {capture['classification_confidence']:.0%}")
    lines.append("")
    lines.append(f"Wrong type? /capture correct {capture['id']} <type> — one of: {', '.join(CAPTURE_TYPES)}")
    return "\n".join(lines)


def _status_mark(status: str) -> str:
    return {"completed": "✓", "archived": "✗", "in_progress": "→"}.get(status, "•")


def describe_captures(captures: list[dict]) -> str:
    """Render a list of captures as chat text, most recent first."""
    if not captures:
        return "Nothing in the inbox. Try /capture <text> to save a thought."

    lines = ["Inbox:"]
    for c in captures:
        mark = _status_mark(c["status"])
        extra = []
        if c["project"]:
            extra.append(c["project"])
        if c["scheduled_for"]:
            extra.append(c["scheduled_for"])
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"  {mark} #{c['id']} [{c['type']}] {c['raw_text']}{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bot command entry point — /capture, with subcommands mirroring the
# established /tasks, /expenses, /mood pattern (bot/commands.py).
# ---------------------------------------------------------------------------

_USAGE = (
    "Usage: /capture <text> | list [status] | correct <id> <type> | "
    "done <id> | archive <id> | schedule <id> <YYYY-MM-DD>"
)


def handle_capture_command(
    chat_id: str, args: list[str], source: str = "unknown", source_message_id: Optional[str] = None
) -> str:
    """Route /capture and its subcommands — see _USAGE for the full shape."""
    if not args:
        return _USAGE

    sub = args[0].lower()

    if sub == "list":
        status = args[1] if len(args) > 1 else None
        if status is not None and status not in CAPTURE_STATUSES:
            return f"Unknown status. Choose one of: {', '.join(CAPTURE_STATUSES)}"
        return describe_captures(list_captures(chat_id, status=status))

    if sub == "correct":
        if len(args) < 3 or not args[1].isdigit():
            return f"Usage: /capture correct <id> <type> — one of: {', '.join(CAPTURE_TYPES)}"
        capture = correct_capture(chat_id, int(args[1]), type_=args[2])
        if capture is None:
            return f"No capture #{args[1]} found, or '{args[2]}' isn't a known type."
        return describe_capture(capture)

    if sub == "done":
        if len(args) < 2 or not args[1].isdigit():
            return "Usage: /capture done <id>"
        return _apply_status(chat_id, int(args[1]), "completed")

    if sub == "archive":
        if len(args) < 2 or not args[1].isdigit():
            return "Usage: /capture archive <id>"
        return _apply_status(chat_id, int(args[1]), "archived")

    if sub == "schedule":
        if len(args) < 3 or not args[1].isdigit():
            return "Usage: /capture schedule <id> <YYYY-MM-DD>"
        capture = schedule_capture(chat_id, int(args[1]), args[2])
        if capture is None:
            return f"No capture #{args[1]} found, or '{args[2]}' isn't a valid date (use YYYY-MM-DD)."
        return describe_capture(capture)

    # Anything else: treat the whole argument string as the text to capture.
    raw_text = " ".join(args).strip()
    if not raw_text:
        return _USAGE
    capture = add_capture(chat_id, raw_text, source=source, source_message_id=source_message_id)
    return describe_capture(capture)


def _apply_status(chat_id: str, capture_id: int, status: str) -> str:
    try:
        capture = update_status(chat_id, capture_id, status)
    except ValueError as exc:
        return str(exc)
    if capture is None:
        return f"No capture #{capture_id} found."
    return describe_capture(capture)
