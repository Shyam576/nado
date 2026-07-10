"""
bot/telegram_bot.py — Telegram transport for Jarvis.

Receives messages via long-polling, enforces the chat_id allowlist (the only
auth boundary — see config.TELEGRAM_ALLOWED_CHAT_IDS), routes recognised
slash commands through bot.commands.dispatch() (shared with every other
transport), and falls through to the LLM chat path (brain.ask) for
everything else. All data is stored under the shared config.OWNER_ID, not
the platform-specific chat_id — see config.py's "Multi-platform identity"
section for why.

Uses PTB's lower-level async API (initialize/start/start_polling) rather
than the Application.run_polling() convenience wrapper, since that wrapper
owns its own event loop and can't run alongside Discord's client in the same
process — see main.py's bot_mode(), which drives both via asyncio.gather.

Run with:  python main.py bot
"""

import asyncio
import datetime
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from bot import notifier
from bot.commands import dispatch
from brain import ask
from config import OWNER_ID, TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
from modules import digest, expenses, finance, habits, tasks, transcription

logger = logging.getLogger(__name__)

REMINDER_POLL_SECONDS = 30
GOLD_TARGET_POLL_SECONDS = 300
HABIT_GAP_POLL_SECONDS = 21600  # 6 hours — a daily-cadence check doesn't need finer polling
PRICE_SNAPSHOT_POLL_SECONDS = 3600  # hourly — gives digest.py a same-day-granularity baseline
DAILY_DIGEST_HOUR = 7  # local time, 24h clock


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle one incoming Telegram message: authenticate, dispatch, reply."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return

    chat_id = chat.id
    if chat_id not in TELEGRAM_ALLOWED_CHAT_IDS:
        logger.warning("Rejected message from unauthorised chat_id=%s", chat_id)
        return  # silently ignore — do not confirm the bot exists to strangers

    text = message.text.strip()
    logger.info("Received from chat_id=%s: %s", chat_id, text)

    try:
        reply = dispatch(OWNER_ID, text)
        if reply is None:
            reply = ask(text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling message: %s", exc)
        reply = "Something went wrong on my end. Give me a moment and try again."

    try:
        await message.reply_text(reply)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send reply (len=%d): %s", len(reply), exc)
        await message.reply_text("Something went wrong sending that reply.")


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle an incoming photo: authenticate, OCR + log as an expense, reply."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.photo:
        return

    chat_id = chat.id
    if chat_id not in TELEGRAM_ALLOWED_CHAT_IDS:
        logger.warning("Rejected photo from unauthorised chat_id=%s", chat_id)
        return

    logger.info("Received photo from chat_id=%s", chat_id)

    try:
        largest_photo = message.photo[-1]  # Telegram sends multiple sizes; last is highest-res
        telegram_file = await largest_photo.get_file()

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir) / "receipt.jpg"
            await telegram_file.download_to_drive(custom_path=str(local_path))
            reply = expenses.add_expense_from_image(OWNER_ID, str(local_path), caption=message.caption)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling photo: %s", exc)
        reply = "Something went wrong processing that image. Give me a moment and try again."

    try:
        await message.reply_text(reply)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send photo reply: %s", exc)


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle an incoming voice note or audio file: transcribe, then process like text."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    chat_id = chat.id
    if chat_id not in TELEGRAM_ALLOWED_CHAT_IDS:
        logger.warning("Rejected voice message from unauthorised chat_id=%s", chat_id)
        return

    voice_or_audio = message.voice or message.audio
    if voice_or_audio is None:
        return

    logger.info("Received voice/audio from chat_id=%s", chat_id)

    try:
        telegram_file = await voice_or_audio.get_file()
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir) / "voice.ogg"
            await telegram_file.download_to_drive(custom_path=str(local_path))
            transcript = transcription.transcribe_audio_file(str(local_path))

        if not transcript:
            await message.reply_text("Couldn't make out any speech in that — try again?")
            return

        reply = dispatch(OWNER_ID, transcript)
        if reply is None:
            reply = ask(transcript)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling voice message: %s", exc)
        await message.reply_text("Something went wrong processing that voice message.")
        return

    try:
        await message.reply_text(f"Heard: “{transcript}”\n\n{reply}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send voice reply: %s", exc)


async def _deliver_due_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast any reminders whose fire time has passed."""
    for reminder in tasks.get_due_reminders():
        try:
            await notifier.broadcast(f"Reminder: {reminder['message']}")
            tasks.mark_delivered(reminder["id"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to deliver reminder id=%s: %s", reminder["id"], exc)


async def _check_gold_target(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast if the stored gold price target has been reached."""
    alert = finance.check_price_target()
    if alert:
        await notifier.broadcast(alert)


async def _check_ter_targets(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast on any reached TER buy/sell price targets."""
    for alert in finance.check_ter_targets():
        await notifier.broadcast(alert)


async def _check_habit_gaps(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast on any habit gaps."""
    for _chat_id, alert in habits.check_habit_gaps():
        await notifier.broadcast(alert)


async def _record_price_snapshots(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: store an hourly gold/TER price snapshot for digest.py."""
    try:
        finance.record_price_snapshots()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to record price snapshots: %s", exc)


async def _send_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast the daily digest."""
    try:
        text = digest.build_daily_digest(OWNER_ID)
        await notifier.broadcast(text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build/deliver daily digest: %s", exc)


def build_application() -> Application:
    """Construct and configure the Telegram Application (does not connect yet)."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT, _handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, _handle_photo))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, _handle_voice))
    application.job_queue.run_repeating(
        _deliver_due_reminders, interval=REMINDER_POLL_SECONDS, first=REMINDER_POLL_SECONDS
    )
    application.job_queue.run_repeating(
        _check_gold_target, interval=GOLD_TARGET_POLL_SECONDS, first=GOLD_TARGET_POLL_SECONDS
    )
    application.job_queue.run_repeating(
        _check_ter_targets, interval=GOLD_TARGET_POLL_SECONDS, first=GOLD_TARGET_POLL_SECONDS
    )
    application.job_queue.run_repeating(
        _check_habit_gaps, interval=HABIT_GAP_POLL_SECONDS, first=HABIT_GAP_POLL_SECONDS
    )
    application.job_queue.run_repeating(
        _record_price_snapshots, interval=PRICE_SNAPSHOT_POLL_SECONDS, first=10
    )
    local_tz = datetime.datetime.now().astimezone().tzinfo
    application.job_queue.run_daily(
        _send_daily_digest, time=datetime.time(hour=DAILY_DIGEST_HOUR, minute=0, tzinfo=local_tz)
    )
    return application


async def run(application: Application) -> None:
    """Start the Telegram bot and block (until cancelled) via long-polling.

    Args:
        application: An Application built by build_application() — passed in
                     rather than constructed here so main.py can register it
                     with notifier before the connection starts.
    """
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Export it before running bot mode.")
    if not TELEGRAM_ALLOWED_CHAT_IDS:
        logger.warning("JARVIS_ALLOWED_CHAT_IDS is empty — every incoming message will be rejected.")

    notifier.register_telegram(application.bot)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Telegram bot starting (long-polling)...")

    try:
        await asyncio.Event().wait()  # run until the task is cancelled by main.py
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
