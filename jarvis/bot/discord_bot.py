"""
bot/discord_bot.py — Discord transport for Jarvis.

Mirrors bot/telegram_bot.py's shape: authenticate, dispatch through
bot.commands.dispatch() (shared with every other transport), fall through to
the LLM chat path for anything else. Auth here is channel-scoped (matches the
channel ID you gave, not a personal-DM model) — only messages posted in an
allowlisted channel are answered, everything else is silently ignored.

Catch-up on reconnect: unlike Telegram (which queues missed updates
server-side via getUpdates), Discord's gateway only pushes live events — a
message sent while this process is offline (laptop asleep, network down)
is NOT automatically redelivered on reconnect. To close that gap, on_ready
scans each allowed channel's history back to the last message this bot
actually processed (tracked via memory.py) and replays anything missed
through the same _process_message() path as live messages.

Run with:  python main.py bot
"""

import logging
import tempfile
from pathlib import Path

import discord

import memory
from bot import notifier
from bot.commands import dispatch
from brain import ask
from config import (
    DISCORD_ALLOWED_CHANNEL_IDS,
    DISCORD_ALLOWED_USER_IDS,
    DISCORD_BOT_TOKEN,
    OWNER_ID,
)
from modules import expenses, intent, transcription

logger = logging.getLogger(__name__)

_DISCORD_MESSAGE_LIMIT = 2000
_CHUNK_SIZE = 1900  # headroom under Discord's 2000-char cap
_CATCHUP_SCAN_LIMIT = 200  # cap how far back we scan on reconnect, per channel


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


def _last_seen_key(channel_id: int) -> str:
    return f"discord_last_seen_message_id_{channel_id}"


async def _handle_receipt_image(message: discord.Message, attachment: discord.Attachment) -> None:
    """OCR + log a payment-screenshot attachment as an expense, then reply."""
    logger.info("Received image from Discord channel_id=%s: %s", message.channel.id, attachment.filename)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir) / attachment.filename
            await attachment.save(local_path)
            caption = message.content.strip() or None
            reply = expenses.add_expense_from_image(OWNER_ID, str(local_path), caption=caption)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling Discord image: %s", exc)
        reply = "Something went wrong processing that image. Give me a moment and try again."

    try:
        await message.channel.send(reply)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send Discord image reply: %s", exc)


async def _handle_voice_attachment(message: discord.Message, attachment: discord.Attachment) -> None:
    """Transcribe an audio/voice attachment, then process the transcript like text."""
    logger.info("Received audio from Discord channel_id=%s: %s", message.channel.id, attachment.filename)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir) / attachment.filename
            await attachment.save(local_path)
            transcript = transcription.transcribe_audio_file(str(local_path))

        if not transcript:
            await message.channel.send("Couldn't make out any speech in that — try again?")
            return

        image_path = None
        reply = dispatch(OWNER_ID, transcript)
        if reply is None:
            routed = intent.route(OWNER_ID, transcript)
            if routed is not None:
                reply, image_path = routed.text, routed.image_path
        if reply is None:
            reply = ask(transcript)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling Discord voice message: %s", exc)
        await message.channel.send("Something went wrong processing that voice message.")
        return

    try:
        chunks = _chunk(f"Heard: “{transcript}”\n\n{reply}")
        if image_path:
            await message.channel.send(chunks[0], file=discord.File(image_path))
            chunks = chunks[1:]
        for chunk in chunks:
            await message.channel.send(chunk)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send Discord voice reply: %s", exc)


async def _process_message(message: discord.Message, client_user) -> None:
    """Authenticate and handle one message — shared by live on_message and catch-up replay.

    Args:
        message: The Discord message to process.
        client_user: The bot's own user (to skip its own messages).
    """
    if message.author == client_user:
        return
    if message.channel.id not in DISCORD_ALLOWED_CHANNEL_IDS:
        return
    if message.author.id not in DISCORD_ALLOWED_USER_IDS:
        # Channel allowlisting alone is not authentication — any server member,
        # webhook, or bot posting in the channel would otherwise reach the
        # laptop actions. Silently ignore non-owners.
        logger.warning(
            "Ignored message from unauthorised Discord user_id=%s in channel_id=%s",
            message.author.id, message.channel.id,
        )
        return

    image_attachments = [
        a for a in message.attachments if (a.content_type or "").startswith("image/")
    ]
    if image_attachments:
        await _handle_receipt_image(message, image_attachments[0])
        return

    audio_attachments = [
        a for a in message.attachments if (a.content_type or "").startswith("audio/")
    ]
    if audio_attachments:
        await _handle_voice_attachment(message, audio_attachments[0])
        return

    text = message.content.strip()
    if not text:
        return

    logger.info("Received from Discord channel_id=%s: %s", message.channel.id, text)

    image_path = None
    try:
        reply = dispatch(OWNER_ID, text)
        if reply is None:
            routed = intent.route(OWNER_ID, text)
            if routed is not None:
                reply, image_path = routed.text, routed.image_path
        if reply is None:
            reply = ask(text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling Discord message: %s", exc)
        reply = "Something went wrong on my end. Give me a moment and try again."

    try:
        chunks = _chunk(reply)
        if image_path:
            await message.channel.send(chunks[0], file=discord.File(image_path))
            chunks = chunks[1:]
        for chunk in chunks:
            await message.channel.send(chunk)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send Discord reply (len=%d): %s", len(reply), exc)
        await message.channel.send("Something went wrong sending that reply.")


async def _catch_up(client: discord.Client) -> None:
    """On (re)connect, replay any messages posted while this process was offline.

    For each allowlisted channel, fetches history newer than the last
    message we actually processed and runs it through _process_message() in
    chronological order — same effect as if the bot had been online the
    whole time. Updates the "last seen" marker as it goes so nothing is
    replayed twice, and safely no-ops on first-ever run (nothing to compare
    against yet, just establishes the baseline).
    """
    for channel_id in DISCORD_ALLOWED_CHANNEL_IDS:
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Catch-up: failed to fetch channel %s: %s", channel_id, exc)
            continue

        last_seen = memory.get_preference(_last_seen_key(channel_id))
        after = discord.Object(id=int(last_seen)) if last_seen else None

        try:
            missed = [
                msg async for msg in channel.history(
                    after=after, limit=_CATCHUP_SCAN_LIMIT, oldest_first=True
                )
            ]
        except Exception as exc:  # noqa: BLE001
            logger.exception("Catch-up: failed to fetch history for channel %s: %s", channel_id, exc)
            continue

        if last_seen and missed:
            logger.info("Catch-up: replaying %d missed message(s) in channel %s", len(missed), channel_id)
        for msg in missed:
            if last_seen:  # skip replay on the very first run — just establish the baseline
                await _process_message(msg, client.user)
            memory.set_preference(_last_seen_key(channel_id), str(msg.id))

        if not missed:
            # Nothing missed, but still need a baseline on first run — use the
            # channel's current last message so future reconnects have something to diff against.
            if not last_seen:
                try:
                    latest = [msg async for msg in channel.history(limit=1)]
                    if latest:
                        memory.set_preference(_last_seen_key(channel_id), str(latest[0].id))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Catch-up: failed to establish baseline for channel %s: %s", channel_id, exc)


def build_client() -> discord.Client:
    """Construct and configure the Discord client (does not connect yet)."""
    intents = discord.Intents.default()
    intents.message_content = True  # required to read message text, not just metadata
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info("Discord bot connected as %s", client.user)
        await _catch_up(client)

    @client.event
    async def on_message(message: discord.Message):
        await _process_message(message, client.user)
        if message.channel.id in DISCORD_ALLOWED_CHANNEL_IDS:
            memory.set_preference(_last_seen_key(message.channel.id), str(message.id))

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
