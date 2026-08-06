"""tests/test_activity.py — ambient activity tracking, without real AppleScript calls."""

import datetime

import memory
from modules import activity
from store.db import get_connection


def _insert_sample(chat_id: str, app_name: str, window_title: str, when: datetime.datetime) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO activity_log (chat_id, app_name, window_title, captured_at) VALUES (?, ?, ?, ?)",
            (chat_id, app_name, window_title, when.isoformat()),
        )


def test_sample_frontmost_stores_a_row(monkeypatch):
    monkeypatch.setattr(activity, "_run_applescript", lambda script: "iTerm2||jarvis session")
    activity.sample_frontmost("owner")

    with get_connection() as conn:
        row = conn.execute("SELECT app_name, window_title FROM activity_log WHERE chat_id = ?", ("owner",)).fetchone()
    assert row["app_name"] == "iTerm2"
    assert row["window_title"] == "jarvis session"


def test_sample_frontmost_noop_on_applescript_failure(monkeypatch):
    def _boom(script):
        raise RuntimeError("osascript: not authorized")

    monkeypatch.setattr(activity, "_run_applescript", _boom)
    activity.sample_frontmost("owner")  # must not raise

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM activity_log WHERE chat_id = ?", ("owner",)).fetchone()[0]
    assert count == 0


def test_sample_frontmost_noop_on_malformed_output(monkeypatch):
    monkeypatch.setattr(activity, "_run_applescript", lambda script: "no delimiter here")
    activity.sample_frontmost("owner")

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM activity_log WHERE chat_id = ?", ("owner",)).fetchone()[0]
    assert count == 0


def test_today_summary_reports_no_data():
    assert activity.today_summary("owner") == "No activity data for today yet."


def test_today_summary_aggregates_minutes_by_app():
    now = datetime.datetime.now()
    for _ in range(3):
        _insert_sample("owner", "Xcode", "main.swift", now)
    for _ in range(1):
        _insert_sample("owner", "Slack", "#general", now)

    result = activity.today_summary("owner")
    assert "Xcode: 15m (main.swift)" in result
    assert "Slack: 5m (#general)" in result
    # Xcode (more samples) should be listed before Slack
    assert result.index("Xcode") < result.index("Slack")


def test_today_summary_excludes_other_days():
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    _insert_sample("owner", "Xcode", "old.swift", yesterday)
    assert activity.today_summary("owner") == "No activity data for today yet."


def test_today_summary_scoped_to_chat_id():
    now = datetime.datetime.now()
    _insert_sample("someone_else", "Xcode", "main.swift", now)
    assert activity.today_summary("owner") == "No activity data for today yet."


def test_check_long_stretch_detects_sustained_same_window():
    now = datetime.datetime.now()
    for i in range(activity._LONG_STRETCH_SAMPLES):
        _insert_sample("owner", "Xcode", "main.swift", now - datetime.timedelta(minutes=i * 5))

    nudge = activity.check_long_stretch("owner")
    assert nudge is not None
    assert "Xcode" in nudge
    assert "main.swift" in nudge


def test_check_long_stretch_silent_below_threshold():
    now = datetime.datetime.now()
    for i in range(activity._LONG_STRETCH_SAMPLES - 1):
        _insert_sample("owner", "Xcode", "main.swift", now - datetime.timedelta(minutes=i * 5))
    assert activity.check_long_stretch("owner") is None


def test_check_long_stretch_silent_when_app_switches():
    now = datetime.datetime.now()
    for i in range(activity._LONG_STRETCH_SAMPLES):
        app = "Xcode" if i % 2 == 0 else "Slack"
        _insert_sample("owner", app, "window", now - datetime.timedelta(minutes=i * 5))
    assert activity.check_long_stretch("owner") is None


def test_check_long_stretch_dedupes_within_cooldown():
    now = datetime.datetime.now()
    for i in range(activity._LONG_STRETCH_SAMPLES):
        _insert_sample("owner", "Xcode", "main.swift", now - datetime.timedelta(minutes=i * 5))

    first = activity.check_long_stretch("owner")
    assert first is not None
    second = activity.check_long_stretch("owner")
    assert second is None


def test_today_summary_non_macos(monkeypatch):
    monkeypatch.setattr(activity.sys, "platform", "linux")

    called = False

    def _fail(script):
        nonlocal called
        called = True

    monkeypatch.setattr(activity, "_run_applescript", _fail)
    activity.sample_frontmost("owner")
    assert not called
