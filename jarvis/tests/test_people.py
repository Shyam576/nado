"""tests/test_people.py — facts, birthdays, last-contact, and the two
proactive checks (follow-up gaps, birthdays landing today)."""

import datetime

from modules import people
from store.db import get_connection


def test_add_fact_creates_person_and_confirms():
    reply = people.add_fact("owner", ["Sarah", "loves", "hiking"])
    assert reply == "Noted about Sarah: loves hiking"


def test_add_fact_requires_name_and_fact():
    assert "Usage" in people.add_fact("owner", ["Sarah"])
    assert "Usage" in people.add_fact("owner", [])


def test_add_fact_is_case_insensitive_on_existing_person():
    people.add_fact("owner", ["Sarah", "loves", "hiking"])
    people.add_fact("owner", ["sarah", "works", "at", "Acme"])

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM people WHERE chat_id = 'owner'").fetchone()[0]
    assert count == 1


def test_show_person_lists_facts_and_defaults():
    people.add_fact("owner", ["Sarah", "loves", "hiking"])

    summary = people.show_person("owner", ["Sarah"])
    assert "Sarah" in summary
    assert "loves hiking" in summary
    assert "never marked" in summary


def test_show_person_not_found():
    reply = people.show_person("owner", ["Nobody"])
    assert "No one named 'Nobody'" in reply


def test_list_people_empty():
    assert "No one tracked yet" in people.list_people("owner", [])


def test_list_people_shows_everyone():
    people.add_fact("owner", ["Sarah", "loves", "hiking"])
    people.add_fact("owner", ["Tom", "plays", "guitar"])

    listing = people.list_people("owner", [])
    assert "Sarah" in listing
    assert "Tom" in listing


def test_set_birthday_valid():
    reply = people.set_birthday("owner", ["Sarah", "03-21"])
    assert "03-21" in reply


def test_set_birthday_rejects_invalid_date():
    reply = people.set_birthday("owner", ["Sarah", "13-40"])
    assert "isn't a valid MM-DD date" in reply


def test_mark_contacted_updates_timestamp():
    people.mark_contacted("owner", ["Sarah"])
    summary = people.show_person("owner", ["Sarah"])
    assert "Last contacted: 0 day(s) ago" in summary


def test_check_followups_fires_after_gap_and_dedupes():
    people.add_fact("owner", ["Sarah", "loves", "hiking"])
    old = (datetime.datetime.now() - datetime.timedelta(days=20)).isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE people SET created_at = ? WHERE name = 'Sarah'", (old,))

    alerts = people.check_followups()
    assert len(alerts) == 1
    assert alerts[0][0] == "owner"
    assert "Sarah" in alerts[0][1]

    assert people.check_followups() == []  # already alerted today


def test_check_followups_skips_recent_contact():
    people.add_fact("owner", ["Sarah", "loves", "hiking"])
    people.mark_contacted("owner", ["Sarah"])

    assert people.check_followups() == []


def test_check_birthdays_fires_only_today_and_dedupes():
    people.add_fact("owner", ["Sarah", "loves", "hiking"])
    today_md = datetime.date.today().strftime("%m-%d")
    with get_connection() as conn:
        conn.execute("UPDATE people SET birthday = ? WHERE name = 'Sarah'", (today_md,))

    alerts = people.check_birthdays()
    assert len(alerts) == 1
    assert "Sarah" in alerts[0][1]

    assert people.check_birthdays() == []  # already alerted this year


def test_check_birthdays_ignores_other_dates():
    people.set_birthday("owner", ["Sarah", "01-01"])
    if datetime.date.today().strftime("%m-%d") == "01-01":
        return  # skip on the one day this fixture would coincidentally fire
    assert people.check_birthdays() == []
