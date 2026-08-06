"""
watchdog.py — force-exit if the shared asyncio event loop stops responding.

discord.py's gateway keepalive (discord.gateway.KeepAliveHandler) runs on
its own daemon thread, not the asyncio event loop — so it keeps ticking and
logging even when the loop itself is completely wedged. Confirmed in
production: "Shard ID None heartbeat blocked for more than N seconds" kept
logging (N climbing every ~10s) for 90+ minutes while the bot was
unresponsive on both Discord and Telegram, since both transports share one
event loop (see main.py's _run_bot_transports). Looking at discord.py's own
source (gateway.py's KeepAliveHandler.run()): once the loop stops picking
up the scheduled heartbeat coroutine, that thread just loops forever
logging the warning — there is no self-recovery built in.

install() attaches a logging.Handler to the discord.gateway logger that
watches for that exact warning and, once blocked past
BLOCKED_THRESHOLD_SECONDS, force-exits the process via os._exit() rather
than attempting a graceful shutdown — a graceful shutdown needs the very
event loop that's stuck. The LaunchAgent's KeepAlive=true then restarts
the process fresh (see ~/Library/LaunchAgents/com.jarvis.bot.plist).
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# Give real transient hiccups (a slow LLM call, a brief network blip) room
# to clear on their own — discord.py logs this warning every ~10s once
# blocked, so this is roughly 6+ consecutive warnings before treating the
# loop as wedged rather than just slow.
BLOCKED_THRESHOLD_SECONDS = 120

_BLOCK_MSG_RE = re.compile(r"heartbeat blocked for more than (\d+) seconds")


class _GatewayHeartbeatWatchdog(logging.Handler):
    """Force-exits the process once discord.py reports a heartbeat block
    exceeding BLOCKED_THRESHOLD_SECONDS."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return

        match = _BLOCK_MSG_RE.search(message)
        if not match:
            return

        blocked_seconds = int(match.group(1))
        if blocked_seconds < BLOCKED_THRESHOLD_SECONDS:
            return

        logger.critical(
            "Event loop unresponsive for %ds (Discord gateway heartbeat blocked) — "
            "force-exiting so the LaunchAgent restarts the process.",
            blocked_seconds,
        )
        os._exit(1)


def install() -> None:
    """Attach the watchdog to discord.py's gateway logger.

    Safe to call unconditionally — if Discord isn't configured, that logger
    simply never emits anything and this is a no-op. Call once at bot-mode
    startup, before the transports connect.
    """
    logging.getLogger("discord.gateway").addHandler(_GatewayHeartbeatWatchdog())
