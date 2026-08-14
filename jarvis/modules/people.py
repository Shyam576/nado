"""
modules/people.py — Freeform facts about people, birthdays, and last-contact tracking.

Fills the "Memory" brain's people gap from JARVIS_V2_PLAN.md's original vision
(projects/prices/tasks were covered; people never were). A person is
identified by name (case-insensitive lookup, original casing preserved for
display) and accumulates freeform facts over time — same "many timestamped
rows under one entity" shape as mood_log under a habit, or person_facts here
under a person. Two proactive checks (check_followups, check_birthdays)
mirror habits.check_habit_gaps()'s dedup-via-memory-preference pattern.
"""

import datetime
import logging

import memory
from store.db import get_connection

logger = logging.getLogger(__name__)

# A person not contacted (or not marked contacted) for this many days gets a follow-up nudge.
FOLLOWUP_GAP_DAYS = 14


def _find_person(chat_id: str, name: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM people WHERE chat_id = ? AND LOWER(name) = LOWER(?)",
            (chat_id, name),
        ).fetchone()


def _get_or_create_person(chat_id: str, name: str):
    """Return the existing person row for `name`, creating one if needed.

    Case-insensitive match so "/person sarah ..." and "/person Sarah ..."
    resolve to the same person; the name as first typed is what's stored.
    """
    row = _find_person(chat_id, name)
    if row is not None:
        return row

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO people (chat_id, name, created_at) VALUES (?, ?, ?)",
            (chat_id, name, datetime.datetime.now().isoformat()),
        )
    return _find_person(chat_id, name)


def _days_ago(iso_timestamp: str) -> int:
    return (datetime.datetime.now() - datetime.datetime.fromisoformat(iso_timestamp)).days


def add_fact(chat_id: str = "", args: list[str] | None = None) -> str:
    """Jot a freeform fact about a person, creating them if this is the first mention.

    Args:
        chat_id: The chat this person is tracked under.
        args: [name, fact words...] — both required.

    Returns:
        A confirmation, or a usage message if name or fact is missing.
    """
    args = args or []
    if len(args) < 2:
        return "Usage: /person <name> <fact> — e.g. /person Sarah loves hiking"

    name, fact = args[0], " ".join(args[1:])
    person = _get_or_create_person(chat_id, name)

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO person_facts (person_id, fact, created_at) VALUES (?, ?, ?)",
            (person["id"], fact, datetime.datetime.now().isoformat()),
        )

    return f"Noted about {person['name']}: {fact}"


def show_person(chat_id: str = "", args: list[str] | None = None) -> str:
    """Show everything known about one person: birthday, last contact, facts.

    Args:
        chat_id: The chat this person is tracked under.
        args: [name].

    Returns:
        A multi-line summary, or a not-found message.
    """
    args = args or []
    if not args:
        return "Usage: /person show <name>"

    name = args[0]
    person = _find_person(chat_id, name)
    if person is None:
        return f"No one named '{name}' tracked yet. Add a fact with /person {name} <fact>."

    lines = [person["name"]]
    if person["birthday"]:
        lines.append(f"Birthday: {person['birthday']}")
    if person["last_contacted_at"]:
        lines.append(f"Last contacted: {_days_ago(person['last_contacted_at'])} day(s) ago")
    else:
        lines.append("Last contacted: never marked")

    with get_connection() as conn:
        facts = conn.execute(
            "SELECT fact, created_at FROM person_facts WHERE person_id = ? ORDER BY created_at DESC",
            (person["id"],),
        ).fetchall()

    if facts:
        lines.append("Facts:")
        for row in facts:
            when = datetime.datetime.fromisoformat(row["created_at"]).strftime("%b %-d, %Y")
            lines.append(f"  {row['fact']} ({when})")

    return "\n".join(lines)


def list_people(chat_id: str = "", args: list[str] | None = None) -> str:
    """List everyone tracked for this chat, with days since last contact.

    Args:
        chat_id: The chat to look up.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A newline-separated list, or a message if no one is tracked yet.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, last_contacted_at FROM people WHERE chat_id = ? ORDER BY name",
            (chat_id,),
        ).fetchall()

    if not rows:
        return "No one tracked yet. Add someone with /person <name> <fact>."

    lines = []
    for row in rows:
        if row["last_contacted_at"]:
            lines.append(f"{row['name']} — last contacted {_days_ago(row['last_contacted_at'])}d ago")
        else:
            lines.append(f"{row['name']} — never marked contacted")
    return "\n".join(lines)


def set_birthday(chat_id: str = "", args: list[str] | None = None) -> str:
    """Set (or update) a person's birthday.

    Args:
        chat_id: The chat this person is tracked under.
        args: [name, birthday] where birthday is MM-DD, e.g. "03-21".

    Returns:
        A confirmation, or a usage/validation message.
    """
    args = args or []
    if len(args) < 2:
        return "Usage: /person birthday <name> <MM-DD> — e.g. /person birthday Sarah 03-21"

    name, birthday = args[0], args[1]
    try:
        # Validate against an explicit leap year so "02-29" parses correctly —
        # strptime with no year defaults to a non-leap year and would reject it.
        datetime.datetime.strptime(f"2000-{birthday}", "%Y-%m-%d")
    except ValueError:
        return f"'{birthday}' isn't a valid MM-DD date — try e.g. 03-21 for March 21st."

    person = _get_or_create_person(chat_id, name)
    with get_connection() as conn:
        conn.execute("UPDATE people SET birthday = ? WHERE id = ?", (birthday, person["id"]))

    return f"Got it — {person['name']}'s birthday is {birthday}."


def mark_contacted(chat_id: str = "", args: list[str] | None = None) -> str:
    """Mark a person as contacted right now (resets the follow-up gap).

    Args:
        chat_id: The chat this person is tracked under.
        args: [name].

    Returns:
        A confirmation, or a usage message.
    """
    args = args or []
    if not args:
        return "Usage: /person contacted <name>"

    name = args[0]
    person = _get_or_create_person(chat_id, name)
    with get_connection() as conn:
        conn.execute(
            "UPDATE people SET last_contacted_at = ? WHERE id = ?",
            (datetime.datetime.now().isoformat(), person["id"]),
        )

    return f"Marked {person['name']} as contacted today."


def check_followups() -> list[tuple[str, str]]:
    """Check every tracked person for a follow-up gap of FOLLOWUP_GAP_DAYS+.

    Falls back to created_at when a person has never been explicitly marked
    contacted, so newly-added people aren't immediately flagged. Fires at
    most once per calendar day per person (state in memory preferences).

    Returns:
        A list of (chat_id, alert_message) tuples for people newly past the
        follow-up threshold today.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, name, COALESCE(last_contacted_at, created_at) AS reference_at "
            "FROM people"
        ).fetchall()

    today = datetime.date.today()
    alerts: list[tuple[str, str]] = []

    for row in rows:
        gap_days = (today - datetime.datetime.fromisoformat(row["reference_at"]).date()).days
        if gap_days < FOLLOWUP_GAP_DAYS:
            continue

        alert_key = f"person_followup_alerted_{row['chat_id']}_{row['id']}"
        if memory.get_preference(alert_key) == today.isoformat():
            continue  # already alerted today

        memory.set_preference(alert_key, today.isoformat())
        alerts.append(
            (row["chat_id"], f"You haven't caught up with {row['name']} in {gap_days} days.")
        )

    return alerts


def check_birthdays() -> list[tuple[str, str]]:
    """Check every tracked person's birthday against today's date.

    Fires once per person per year (state in memory preferences).

    Returns:
        A list of (chat_id, alert_message) tuples for birthdays landing today.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, name, birthday FROM people WHERE birthday IS NOT NULL"
        ).fetchall()

    today = datetime.date.today()
    today_md = today.strftime("%m-%d")
    alerts: list[tuple[str, str]] = []

    for row in rows:
        if row["birthday"] != today_md:
            continue

        alert_key = f"birthday_alerted_{row['chat_id']}_{row['id']}_{today.year}"
        if memory.get_preference(alert_key):
            continue  # already alerted this year

        memory.set_preference(alert_key, True)
        alerts.append((row["chat_id"], f"Today is {row['name']}'s birthday."))

    return alerts
