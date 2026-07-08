"""
bot/discord_bot.py — Discord transport for Jarvis.

Mirrors bot/telegram_bot.py's shape: authenticate, dispatch through
bot.commands.dispatch() (shared with every other transport), fall through to
the LLM chat path for anything else. Auth here is channel-scoped (matches the
channel ID you gave, not a personal-DM model) — only messages posted in an
allowlisted channel are answered, everything else is silently ignored.

Run with:  python main.py bot
"""

import logging

import discord

from bot import notifier
from bot.commands import dispatch
from brain import ask
from config import DISCORD_ALLOWED_CHANNEL_IDS, DISCORD_BOT_TOKEN, OWNER_ID

logger = logging.getLogger(__name__)

_DISCORD_MESSAGE_LIMIT = 2000
_CHUNK_SIZE = 1900  # headroom under Discord's 2000-char cap


def _chunk(text: str) -> list[str]:
    """Split text into <=_CHUNK_SIZE pieces, breaking on line boundaries where possible."""
    if len(text) <= _DISCORD_MESSAGE_LIMIT:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= _CHUNK_SIZE:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, _CHUNK_SIZE)
        if split_at <= 0:
            split_at = _CHUNK_SIZE
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


def build_client() -> discord.Client:
    """Construct and configure the Discord client (does not connect yet)."""
    intents = discord.Intents.default()
    intents.message_content = True  # required to read message text, not just metadata
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info("Discord bot connected as %s", client.user)

    @client.event
    async def on_message(message: discord.Message):
        if message.author == client.user:
            return
        if message.channel.id not in DISCORD_ALLOWED_CHANNEL_IDS:
            return

        text = message.content.strip()
        if not text:
            return

        logger.info("Received from Discord channel_id=%s: %s", message.channel.id, text)

        try:
            reply = dispatch(OWNER_ID, text)
            if reply is None:
                reply = ask(text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error handling Discord message: %s", exc)
            reply = "Something went wrong on my end. Give me a moment and try again."

        try:
            for chunk in _chunk(reply):
                await message.channel.send(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to send Discord reply (len=%d): %s", len(reply), exc)
            await message.channel.send("Something went wrong sending that reply.")

    return client


async def run(client: discord.Client) -> None:
    """Start the Discord client and register it with the notifier. Blocks until stopped.

    Args:
        client: A client built by build_client() — passed in rather than
                constructed here so main.py can register it with notifier
                before the (blocking) connection starts.
    """
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Export it before running bot mode.")
    if not DISCORD_ALLOWED_CHANNEL_IDS:
        logger.warning("DISCORD_CHANNEL_ID is empty — every incoming message will be ignored.")

    notifier.register_discord(client)
    logger.info("Discord bot starting...")
    await client.start(DISCORD_BOT_TOKEN)
