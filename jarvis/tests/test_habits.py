"""tests/test_habits.py — streak calculation, mood logging, habit-gap alerts."""

import datetime

from modules import habits
from store.db import get_connection


def _log_habit_on(chat_id: str, habit: str, day: datetime.date) -> None:
    """Insert a habit_log row for an arbitrary date, bypassing "today only" in log_habit."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO habit_log (chat_id, habit, completed_at) VALUES (?, ?, ?)",
            (chat_id, habit, datetime.datetime.combine(day, datetime.time(9, 0)).isoformat()),
        )


def test_log_habit_is_idempotent_per_day():
    habits.log_habit("owner", ["gym"])
    habits.log_habit("owner", ["gym"])  # same day again

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM habit_log WHERE chat_id = ? AND habit = ?", ("owner", "gym")
        ).fetchone()[0]
    assert count == 1


def test_streak_counts_consecutive_days_ending_today():
    today = datetime.date.today()
    for offset in range(4):  # today, yesterday, ... 3 days ago
        _log_habit_on("owner", "gym", today - datetime.timedelta(days=offset))

    assert habits._current_streak("owner", "gym") == 4


def test_streak_still_alive_if_yesterday_logged_but_not_today():
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    _log_habit_on("owner", "gym", yesterday)
    _log_habit_on("owner", "gym", yesterday - datetime.timedelta(days=1))

    assert habits._current_streak("owner", "gym") == 2


def test_streak_is_zero_after_a_gap():
    three_days_ago = datetime.date.today() - datetime.timedelta(days=3)
    _log_habit_on("owner", "gym", three_days_ago)

    assert habits._current_streak("owner", "gym") == 0


def test_streak_zero_for_untracked_habit():
    assert habits._current_streak("owner", "meditation") == 0


def test_weekly_habit_adherence_counts_distinct_days_in_window():
    today = datetime.date.today()
    _log_habit_on("owner", "gym", today)
    _log_habit_on("owner", "gym", today - datetime.timedelta(days=2))
    _log_habit_on("owner", "gym", today - datetime.timedelta(days=10))  # outside 7-day window

    result = habits.weekly_habit_adherence("owner")
    assert result == [{"habit": "gym", "days_logged": 2}]


def test_weekly_habit_adherence_empty_when_no_habits():
    assert habits.weekly_habit_adherence("owner") == []


def test_check_habit_gaps_fires_after_threshold_and_dedupes_same_day():
    gap_start = datetime.date.today() - datetime.timedelta(days=4)
    _log_habit_on("owner", "gym", gap_start)

    alerts = habits.check_habit_gaps()
    assert len(alerts) == 1
    assert alerts[0][0] == "owner"
    assert "gym" in alerts[0][1]

    # Second call same day — already alerted, should not fire again
    assert habits.check_habit_gaps() == []


def test_check_habit_gaps_silent_within_threshold():
    _log_habit_on("owner", "gym", datetime.date.today() - datetime.timedelta(days=1))
    assert habits.check_habit_gaps() == []


def test_log_and_history_mood():
    habits.log_mood("owner", ["happy", "8", "great", "day"])
    result = habits.mood_history("owner")
    assert "happy" in result
    assert "8/10" in result
    assert "great day" in result


def test_log_mood_requires_a_mood_word():
    assert "Usage" in habits.log_mood("owner", [])


def test_weekly_mood_entries_excludes_older_than_7_days():
    with get_connection() as conn:
        recent = datetime.datetime.now().isoformat()
        old = (datetime.datetime.now() - datetime.timedelta(days=8)).isoformat()
        conn.execute(
            "INSERT INTO mood_log (chat_id, mood, energy, note, created_at) VALUES (?, 'happy', 7, NULL, ?)",
            ("owner", recent),
        )
        conn.execute(
            "INSERT INTO mood_log (chat_id, mood, energy, note, created_at) VALUES (?, 'sad', 3, NULL, ?)",
            ("owner", old),
        )

    entries = habits.weekly_mood_entries("owner")
    assert len(entries) == 1
    assert entries[0]["mood"] == "happy"
