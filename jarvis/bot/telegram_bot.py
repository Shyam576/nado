"""
bot/telegram_bot.py — Telegram transport for Jarvis.

Receives messages via long-polling, enforces the chat_id allowlist (the only
auth boundary — see config.ALLOWED_CHAT_IDS), routes recognised slash commands
through bot.commands.dispatch(), and falls through to the LLM chat path
(brain.ask) for everything else.

Run with:  python main.py bot
"""

import datetime
import logging

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from bot.commands import dispatch
from brain import ask
from config import ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
from modules import digest, finance, habits, tasks

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
    if chat_id not in ALLOWED_CHAT_IDS:
        logger.warning("Rejected message from unauthorised chat_id=%s", chat_id)
        return  # silently ignore — do not confirm the bot exists to strangers

    text = message.text.strip()
    logger.info("Received from chat_id=%s: %s", chat_id, text)

    try:
        reply = dispatch(str(chat_id), text)
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


async def _deliver_due_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: send any reminders whose fire time has passed.

    Only delivers to chat_ids in the allowlist, even though reminders can
    currently only be created by an allowlisted chat in the first place —
    this keeps the delivery path honest if that ever changes.
    """
    for reminder in tasks.get_due_reminders():
        chat_id = reminder["chat_id"]
        if int(chat_id) not in ALLOWED_CHAT_IDS:
            continue
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"Reminder: {reminder['message']}")
            tasks.mark_delivered(reminder["id"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to deliver reminder id=%s: %s", reminder["id"], exc)


async def _check_gold_target(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: alert if the stored gold price target has been reached.

    memory.py's preference store is global (single-user), so the alert is
    broadcast to every allowlisted chat rather than a specific chat_id — fine
    while there's one real user; revisit if ALLOWED_CHAT_IDS ever grows.
    """
    alert = finance.check_price_target()
    if not alert:
        return
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id=chat_id, text=alert)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to deliver gold target alert to %s: %s", chat_id, exc)


async def _check_ter_targets(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: alert on any reached TER buy/sell price targets."""
    for alert in finance.check_ter_targets():
        for chat_id in ALLOWED_CHAT_IDS:
            try:
                await context.bot.send_message(chat_id=chat_id, text=alert)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to deliver TER target alert to %s: %s", chat_id, exc)


async def _check_habit_gaps(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: alert on any habit gaps, targeted to their own chat_id."""
    for chat_id, alert in habits.check_habit_gaps():
        if int(chat_id) not in ALLOWED_CHAT_IDS:
            continue
        try:
            await context.bot.send_message(chat_id=chat_id, text=alert)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to deliver habit gap alert to %s: %s", chat_id, exc)


async def _record_price_snapshots(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: store an hourly gold/TER price snapshot for digest.py."""
    try:
        finance.record_price_snapshots()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to record price snapshots: %s", exc)


async def _send_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: send the daily digest to every allowlisted chat."""
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            text = digest.build_daily_digest(str(chat_id))
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to deliver daily digest to %s: %s", chat_id, exc)


def run() -> None:
    """Start the Telegram bot and block, polling for updates until interrupted."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Export it before running bot mode."
        )
    if not ALLOWED_CHAT_IDS:
        logger.warning(
            "JARVIS_ALLOWED_CHAT_IDS is empty — every incoming message will be rejected."
        )

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT, _handle_message))
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

    logger.info("Telegram bot starting (long-polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
