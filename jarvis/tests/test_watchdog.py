"""tests/test_watchdog.py — event-loop-stuck watchdog, without actually exiting.

Every test monkeypatches watchdog.os._exit to a recorder instead of the
real thing — calling the genuine os._exit() would kill the pytest process.
"""

import logging

import pytest

import watchdog


def _make_record(message: str, logger_name: str = "discord.gateway", level=logging.WARNING):
    return logging.LogRecord(
        name=logger_name, level=level, pathname=__file__, lineno=1, msg=message, args=(), exc_info=None
    )


@pytest.fixture
def fake_exit(monkeypatch):
    calls = []
    monkeypatch.setattr(watchdog.os, "_exit", lambda code: calls.append(code))
    return calls


def test_emit_ignores_unrelated_messages(fake_exit):
    handler = watchdog._GatewayHeartbeatWatchdog()
    handler.emit(_make_record("Shard ID None has connected to Gateway."))
    assert fake_exit == []


def test_emit_ignores_short_block(fake_exit):
    handler = watchdog._GatewayHeartbeatWatchdog()
    handler.emit(_make_record("Shard ID None heartbeat blocked for more than 10 seconds."))
    assert fake_exit == []


def test_emit_ignores_block_just_under_threshold(fake_exit):
    handler = watchdog._GatewayHeartbeatWatchdog()
    message = f"Shard ID None heartbeat blocked for more than {watchdog.BLOCKED_THRESHOLD_SECONDS - 10} seconds."
    handler.emit(_make_record(message))
    assert fake_exit == []


def test_emit_exits_at_threshold(fake_exit):
    handler = watchdog._GatewayHeartbeatWatchdog()
    message = f"Shard ID None heartbeat blocked for more than {watchdog.BLOCKED_THRESHOLD_SECONDS} seconds."
    handler.emit(_make_record(message))
    assert fake_exit == [1]


def test_emit_exits_on_real_production_message(fake_exit):
    """The exact message format observed in production, well past the threshold."""
    handler = watchdog._GatewayHeartbeatWatchdog()
    handler.emit(_make_record("Shard ID None heartbeat blocked for more than 5470 seconds."))
    assert fake_exit == [1]


def test_emit_matches_message_with_appended_stack_trace(fake_exit):
    """discord.py sometimes appends a loop-thread stack trace after the block message."""
    handler = watchdog._GatewayHeartbeatWatchdog()
    message = (
        f"Shard ID None heartbeat blocked for more than {watchdog.BLOCKED_THRESHOLD_SECONDS + 50} seconds.\n"
        "Loop thread traceback (most recent call last):\n"
        '  File "example.py", line 1, in <module>\n'
    )
    handler.emit(_make_record(message))
    assert fake_exit == [1]


def test_emit_ignores_messages_from_other_loggers(fake_exit):
    handler = watchdog._GatewayHeartbeatWatchdog()
    message = f"heartbeat blocked for more than {watchdog.BLOCKED_THRESHOLD_SECONDS} seconds."
    # Handler itself doesn't filter by logger name (that's done by attaching
    # it only to discord.gateway in install()) — verify emit() still matches
    # correctly regardless of which logger record it's given, since install()
    # is what scopes it.
    handler.emit(_make_record(message, logger_name="some.other.logger"))
    assert fake_exit == [1]


def test_install_attaches_handler_to_discord_gateway_logger():
    before = len(logging.getLogger("discord.gateway").handlers)
    watchdog.install()
    after = logging.getLogger("discord.gateway").handlers
    assert len(after) == before + 1
    assert isinstance(after[-1], watchdog._GatewayHeartbeatWatchdog)
    # Clean up so repeated test runs / other tests don't accumulate handlers
    logging.getLogger("discord.gateway").removeHandler(after[-1])
