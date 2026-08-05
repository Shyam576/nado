"""tests/test_calendar_app.py — Calendar.app AppleScript integration, mocked.

Real end-to-end AppleScript behavior (parsing, sorting, time formatting)
was verified manually against the actual Calendar.app on the dev machine
before writing these — these tests cover the Python-side logic with a
mocked _run_applescript so the suite doesn't depend on Calendar.app data
or run only on macOS.
"""

import pytest

from modules import calendar_app


def test_today_events_reports_no_events(monkeypatch):
    monkeypatch.setattr(calendar_app, "_run_applescript", lambda script: "")
    assert calendar_app.today_events() == "No events on your calendar today."


def test_today_events_parses_and_sorts_by_time(monkeypatch):
    raw = "14:00||Afternoon sync\n09:30||Morning standup\n14:00||Second 2pm thing\n"
    monkeypatch.setattr(calendar_app, "_run_applescript", lambda script: raw)

    result = calendar_app.today_events()
    lines = result.splitlines()
    assert lines[0] == "Today's events:"
    assert "9:30 AM — Morning standup" in lines[1]
    assert "2:00 PM — Afternoon sync" in lines[2]
    assert "2:00 PM — Second 2pm thing" in lines[3]


def test_today_events_handles_untitled_event(monkeypatch):
    monkeypatch.setattr(calendar_app, "_run_applescript", lambda script: "10:00||\n")
    result = calendar_app.today_events()
    assert "(untitled event)" in result


def test_today_events_handles_applescript_failure(monkeypatch):
    def _boom(script):
        raise RuntimeError("osascript: not authorized")

    monkeypatch.setattr(calendar_app, "_run_applescript", _boom)
    result = calendar_app.today_events()
    assert "Couldn't read Calendar.app" in result
    assert "Automation" in result


def test_today_events_skips_malformed_lines(monkeypatch):
    raw = "not a valid line\n09:00||Real event\n"
    monkeypatch.setattr(calendar_app, "_run_applescript", lambda script: raw)
    result = calendar_app.today_events()
    assert "Real event" in result
    assert result.count("—") == 1


@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ("00:00", "12:00 AM"),
        ("09:05", "9:05 AM"),
        ("12:00", "12:00 PM"),
        ("14:30", "2:30 PM"),
        ("23:59", "11:59 PM"),
    ],
)
def test_format_time(hhmm, expected):
    assert calendar_app._format_time(hhmm) == expected


def test_today_events_non_macos(monkeypatch):
    monkeypatch.setattr(calendar_app.sys, "platform", "linux")
    assert "only wired up for macOS" in calendar_app.today_events()
