"""
bot/notifier.py — Cross-platform delivery for proactive messages.

Background jobs (reminders, price alerts, daily digest, ...) don't know or
care which chat platform(s) are actually running — they just call
notifier.broadcast(text) and it fans out to every configured, allowlisted
recipient across Telegram and Discord. Each transport registers itself here
once it's started; a transport that never registers is simply skipped.
"""

import logging

from config import DISCORD_ALLOWED_CHANNEL_IDS, TELEGRAM_ALLOWED_CHAT_IDS

logger = logging.getLogger(__name__)

_telegram_bot = None  # a telegram.Bot (or Application.bot), set by telegram_bot.run()
_discord_client = None  # a discord.Client, set by discord_bot.run()


def register_telegram(bot) -> None:
    """Register the running Telegram bot instance for outbound delivery."""
    global _telegram_bot
    _telegram_bot = bot


def register_discord(client) -> None:
    """Register the running Discord client instance for outbound delivery."""
    global _discord_client
    _discord_client = client


async def broadcast(text: str) -> None:
    """Send `text` to every allowlisted recipient on every registered platform.

    Args:
        text: The message to deliver.
    """
    if _telegram_bot is not None:
        for chat_id in TELEGRAM_ALLOWED_CHAT_IDS:
            try:
                await _telegram_bot.send_message(chat_id=chat_id, text=text)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Telegram broadcast failed for chat_id=%s: %s", chat_id, exc)

    if _discord_client is not None:
        for channel_id in DISCORD_ALLOWED_CHANNEL_IDS:
            try:
                channel = _discord_client.get_channel(channel_id)
                if channel is None:
                    channel = await _discord_client.fetch_channel(channel_id)
                await channel.send(text)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Discord broadcast failed for channel_id=%s: %s", channel_id, exc)
