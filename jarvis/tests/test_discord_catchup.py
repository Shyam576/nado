"""
tests/test_discord_catchup.py — regression test for the Discord catch-up
duplicate-processing race.

Discord dispatches on_ready as its own asyncio task on every fresh IDENTIFY,
not just once per process — a flaky reconnect right after a laptop wake can
fire it twice in quick succession. Before the fix, two concurrent
_catch_up() calls would both read the same stale "last seen" checkpoint
before either advanced it, so both would replay the same missed message
(e.g. logging the same expense screenshot twice). _catchup_lock in
bot/discord_bot.py serializes _catch_up() so the second call only proceeds
after the first has advanced the checkpoint.
"""

import asyncio

import memory
from bot import discord_bot


class _FakeMessage:
    def __init__(self, msg_id: int):
        self.id = msg_id


class _FakeChannel:
    """Yields one fixed missed message, respecting `after` like a real Discord channel."""

    def __init__(self, message_id: int):
        self._message_id = message_id

    async def history(self, after=None, limit=None, oldest_first=True):
        if after is not None and after.id >= self._message_id:
            return
        yield _FakeMessage(self._message_id)


def test_concurrent_catchup_does_not_replay_same_message_twice(monkeypatch):
    """Two overlapping _catch_up() calls must not both process the same missed message."""
    channel_id = 111
    message_id = 105
    monkeypatch.setattr(discord_bot, "DISCORD_ALLOWED_CHANNEL_IDS", {channel_id})

    # Establish a baseline so _catch_up treats message_id as "missed, replay it".
    memory.set_preference(discord_bot._last_seen_key(channel_id), "100")

    process_started = asyncio.Event()
    release = asyncio.Event()
    processed_ids: list[int] = []

    async def fake_process_message(msg, client_user, is_replay=False):
        # Simulate the real OCR/DB work yielding the event loop mid-flight —
        # this is the window in which the pre-fix code let a second
        # concurrent _catch_up() call read the same stale checkpoint.
        process_started.set()
        await release.wait()
        processed_ids.append(msg.id)

    monkeypatch.setattr(discord_bot, "_process_message", fake_process_message)

    channel = _FakeChannel(message_id)

    class _FakeClient:
        user = object()

        def get_channel(self, cid):
            return channel

    client = _FakeClient()

    async def scenario():
        first = asyncio.ensure_future(discord_bot._catch_up(client))
        await process_started.wait()  # first call is now blocked inside _process_message

        second = asyncio.ensure_future(discord_bot._catch_up(client))
        await asyncio.sleep(0.05)  # give the second call a chance to (wrongly) start replay too

        release.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())

    assert processed_ids == [message_id], (
        f"expected message {message_id} to be replayed exactly once, got {processed_ids}"
    )
    assert memory.get_preference(discord_bot._last_seen_key(channel_id)) == str(message_id)
