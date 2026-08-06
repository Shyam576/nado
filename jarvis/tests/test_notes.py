"""tests/test_notes.py — zero-friction note capture."""

import datetime

from modules import notes
from store.db import get_connection


def test_add_note_requires_text():
    assert "Usage" in notes.add_note("owner", [])


def test_add_note_and_list():
    result = notes.add_note("owner", ["the", "wifi", "password", "is", "on", "the", "router"])
    assert result == "Noted."

    listing = notes.list_notes("owner")
    assert "the wifi password is on the router" in listing


def test_list_notes_reports_empty():
    assert "No notes yet" in notes.list_notes("owner")


def test_list_notes_most_recent_first():
    notes.add_note("owner", ["first", "note"])
    notes.add_note("owner", ["second", "note"])

    listing = notes.list_notes("owner")
    assert listing.index("second note") < listing.index("first note")


def test_list_notes_respects_limit():
    for i in range(5):
        notes.add_note("owner", [f"note{i}"])

    listing = notes.list_notes("owner", ["2"])
    assert listing.count("note") == 2


def test_list_notes_scoped_to_chat_id():
    notes.add_note("someone_else", ["private", "note"])
    assert "No notes yet" in notes.list_notes("owner")


def test_weekly_notes_excludes_older_than_7_days():
    with get_connection() as conn:
        recent = datetime.datetime.now().isoformat()
        old = (datetime.datetime.now() - datetime.timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO notes (chat_id, text, created_at) VALUES (?, 'recent note', ?)", ("owner", recent)
        )
        conn.execute(
            "INSERT INTO notes (chat_id, text, created_at) VALUES (?, 'old note', ?)", ("owner", old)
        )

    result = notes.weekly_notes("owner")
    assert result == ["recent note"]


def test_weekly_notes_empty_when_none():
    assert notes.weekly_notes("owner") == []
