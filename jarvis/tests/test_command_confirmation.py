"""tests/test_command_confirmation.py — shared run_command confirmation gate.

Covers the module directly, plus its two real entry points: actions.py's
voice-mode <ACTION> path and modules/intent.py's bot-mode dispatch path —
both must stage rather than execute, and "yes" from either must actually
run the command via actions.run_command.
"""

import pytest

import actions
import command_confirmation
from modules import intent


@pytest.fixture(autouse=True)
def _clear_pending():
    command_confirmation._pending.clear()
    yield
    command_confirmation._pending.clear()


def test_stage_parks_without_executing(monkeypatch):
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(actions, "run_command", _fail)

    prompt = command_confirmation.stage("owner", "echo hi")
    assert "Run this?" in prompt
    assert "echo hi" in prompt
    assert not called


def test_check_confirms_and_executes(monkeypatch):
    monkeypatch.setattr(actions, "run_command", lambda cmd, **kwargs: "hi")

    command_confirmation.stage("owner", "echo hi")
    result = command_confirmation.check("owner", "yes")
    assert "hi" in result
    # Pending state cleared — a second "yes" has nothing to resolve
    assert command_confirmation.check("owner", "yes") is None


def test_check_with_allow_execution_false_does_not_run(monkeypatch):
    """A replayed 'yes' (Discord catch-up) must never execute the command."""
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(actions, "run_command", _fail)

    command_confirmation.stage("owner", "echo hi")
    result = command_confirmation.check("owner", "yes", allow_execution=False)

    assert not called
    assert "catching up" in result
    assert "echo hi" in result
    # Consumed regardless — a genuine live "yes" after this has nothing left
    # to confirm; re-requesting the command from scratch is required.
    assert command_confirmation.check("owner", "yes") is None


def test_check_cancels():
    command_confirmation.stage("owner", "echo hi")
    result = command_confirmation.check("owner", "no")
    assert result == "Cancelled — nothing was run."


def test_check_expired_window(monkeypatch):
    command_confirmation.stage("owner", "echo hi")
    # Force the queued_at timestamp into the past
    cmd, _ = command_confirmation._pending["owner"]
    command_confirmation._pending["owner"] = (cmd, 0.0)

    result = command_confirmation.check("owner", "yes")
    assert "expired" in result


def test_check_noop_without_pending():
    assert command_confirmation.check("owner", "yes") is None


def test_check_unrelated_message_abandons_pending():
    command_confirmation.stage("owner", "echo hi")
    assert command_confirmation.check("owner", "what's the weather") is None
    # Pending was abandoned — a later "yes" has nothing to confirm
    assert command_confirmation.check("owner", "yes") is None


def test_voice_pipeline_stages_instead_of_executing(monkeypatch):
    """actions.parse_and_execute's run_command branch must stage, not run."""
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(actions, "run_command", _fail)

    reply = '<ACTION>{"type": "run_command", "cmd": "echo hi"}</ACTION>\nRunning that command.'
    action_result, clean_reply = actions.parse_and_execute(reply)

    assert not called
    assert "Run this?" in action_result
    # clean_reply is overridden to the real confirmation prompt — the LLM's
    # stale "Running that command." line must not be what gets spoken.
    assert clean_reply == action_result
    assert "Running that command" not in clean_reply


def test_bot_pipeline_stages_instead_of_executing(monkeypatch):
    """modules/intent.py's run_command dispatch must stage, not run."""
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(actions, "run_command", _fail)

    reply = intent._dispatch_run_command("owner", {"command": "echo hi"})
    assert not called
    assert "Run this?" in reply.text


def test_confirmation_shared_across_voice_and_bot_entry_points(monkeypatch):
    """A command staged via the bot path can be confirmed via the voice path and vice versa."""
    monkeypatch.setattr(actions, "run_command", lambda cmd, **kwargs: "hi")

    intent._dispatch_run_command("owner", {"command": "echo hi"})
    # Resolved through main.py's entry point (command_confirmation.check),
    # not intent.py's — proving both share the same pending state.
    result = command_confirmation.check("owner", "yes")
    assert "hi" in result


def test_intent_route_blocks_confirmation_during_replay(monkeypatch):
    """A stale run_command + 'yes' pair replayed on Discord reconnect must not execute.

    Regression test for the actual production bug: staging happens at
    replay time (now), so the 60s confirmation window is always satisfied
    when both messages replay back-to-back regardless of how old they
    really are — allow_execution=False is what actually stops it.
    """
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(actions, "run_command", _fail)

    intent._dispatch_run_command("owner", {"command": "echo hi"})
    reply = intent.route("owner", "yes", allow_execution=False)

    assert not called
    assert reply is not None
    assert "catching up" in reply.text


def test_intent_route_allows_confirmation_when_live(monkeypatch):
    monkeypatch.setattr(actions, "run_command", lambda cmd, **kwargs: "hi")

    intent._dispatch_run_command("owner", {"command": "echo hi"})
    reply = intent.route("owner", "yes")  # allow_execution defaults to True

    assert reply is not None
    assert "hi" in reply.text
