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
import os
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

import voice
from bot import notifier
from bot.commands import dispatch
from brain import ask
from config import OWNER_ID, TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
from modules import activity, devops, digest, email_watcher, expenses, finance, habits, intent, nudges, tasks, transcription

logger = logging.getLogger(__name__)

REMINDER_POLL_SECONDS = 30
DAILY_REMINDER_POLL_SECONDS = 60  # minute-granularity is enough for "around HH:MM"
GOLD_TARGET_POLL_SECONDS = 300
HABIT_GAP_POLL_SECONDS = 21600  # 6 hours — a daily-cadence check doesn't need finer polling
PRICE_SNAPSHOT_POLL_SECONDS = 3600  # hourly — gives digest.py a same-day-granularity baseline
EMAIL_POLL_SECONDS = 120  # 2 minutes — feels near-real-time without hammering the IMAP server
DAILY_DIGEST_HOUR = 7  # local time, 24h clock
BUDGET_PACING_POLL_SECONDS = 3600  # hourly — threshold state in nudges.py prevents repeats
EVENING_NUDGE_HOUR = 21  # 9 PM — late enough that "no expenses today" is meaningful
STALE_TASK_HOUR = 10  # mid-morning, when acting on a task nudge is most likely
K8S_HEALTH_POLL_SECONDS = 900  # 15 minutes — devops issues deserve faster detection than daily checks
ACTIVITY_SAMPLE_POLL_SECONDS = activity.SAMPLE_INTERVAL_MINUTES * 60


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
        logger.exception("Error handling message: %s", exc)
        reply = "Something went wrong on my end. Give me a moment and try again."

    try:
        if image_path:
            with open(image_path, "rb") as photo:
                await message.reply_photo(photo=photo, caption=reply)
        else:
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

        image_path = None
        reply = dispatch(OWNER_ID, transcript)
        if reply is None:
            routed = intent.route(OWNER_ID, transcript)
            if routed is not None:
                reply, image_path = routed.text, routed.image_path
        if reply is None:
            reply = ask(transcript)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling voice message: %s", exc)
        await message.reply_text("Something went wrong processing that voice message.")
        return

    try:
        text_reply = f"Heard: “{transcript}”\n\n{reply}"
        if image_path:
            with open(image_path, "rb") as photo:
                await message.reply_photo(photo=photo, caption=text_reply)
        else:
            await message.reply_text(text_reply)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send voice reply: %s", exc)

    # Reply in kind: the user sent voice, so also speak the reply back, not
    # just the transcript echo — mirrors the input modality instead of
    # always forcing text-only replies for a voice conversation.
    audio_path = None
    try:
        audio_path = voice.synthesize_to_file(reply)
        if audio_path:
            with open(audio_path, "rb") as audio_file:
                await message.reply_audio(audio=audio_file)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send voice-note reply: %s", exc)
    finally:
        if audio_path:
            try:
                os.unlink(audio_path)
            except OSError:
                pass


async def _deliver_due_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast any reminders whose fire time has passed."""
    for reminder in tasks.get_due_reminders():
        try:
            await notifier.broadcast(f"Reminder: {reminder['message']}")
            tasks.mark_delivered(reminder["id"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to deliver reminder id=%s: %s", reminder["id"], exc)


async def _deliver_due_daily_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast any recurring daily reminders due today."""
    for reminder in tasks.get_due_daily_reminders():
        try:
            await notifier.broadcast(f"Daily reminder: {reminder['message']}")
            tasks.mark_daily_reminder_fired(reminder["id"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to deliver daily reminder id=%s: %s", reminder["id"], exc)


async def _check_gold_target(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast if the stored gold price target has been reached."""
    alert = finance.check_price_target()
    if alert:
        alert += nudges.related_task_note(OWNER_ID, "gold")
        await notifier.broadcast(alert)


async def _check_ter_targets(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast on any reached TER buy/sell price targets."""
    for alert in finance.check_ter_targets():
        alert += nudges.related_task_note(OWNER_ID, "ter")
        await notifier.broadcast(alert)


async def _check_habit_gaps(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast on any habit gaps."""
    for _chat_id, alert in habits.check_habit_gaps():
        await notifier.broadcast(alert)


async def _check_k8s_health(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast + create a task for newly-unhealthy K8s deployments."""
    try:
        for alert in devops.check_k8s_health(OWNER_ID):
            await notifier.broadcast(alert)
    except Exception as exc:  # noqa: BLE001
        logger.exception("K8s health check failed: %s", exc)


async def _check_new_emails(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast any new emails since the last poll."""
    for alert in email_watcher.check_new_emails():
        await notifier.broadcast(alert)


async def _sample_activity(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: record the frontmost app/window, and nudge on a long stretch."""
    try:
        activity.sample_frontmost(OWNER_ID)
        nudge = activity.check_long_stretch(OWNER_ID)
        if nudge:
            await notifier.broadcast(nudge)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Activity sampling failed: %s", exc)


async def _record_price_snapshots(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: store an hourly gold/TER price snapshot for digest.py."""
    try:
        finance.record_price_snapshots()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to record price snapshots: %s", exc)


async def _check_budget_pacing(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: broadcast when month spend crosses a budget threshold."""
    try:
        alert = nudges.budget_pacing_alert(OWNER_ID)
        if alert:
            await notifier.broadcast(alert)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Budget pacing check failed: %s", exc)


async def _send_evening_expense_nudge(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: nudge once in the evening if nothing was logged today."""
    try:
        nudge = nudges.evening_expense_nudge(OWNER_ID)
        if nudge:
            await notifier.broadcast(nudge)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Evening expense nudge failed: %s", exc)


async def _send_activity_recap(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: proactively recap today's activity in the evening, unprompted."""
    try:
        summary = activity.today_summary(OWNER_ID)
        if summary != "No activity data for today yet.":
            await notifier.broadcast(summary)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Activity recap failed: %s", exc)


async def _send_stale_task_nudge(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-queue callback: surface pending tasks untouched for 5+ days (once each)."""
    try:
        nudge = nudges.stale_task_nudge(OWNER_ID)
        if nudge:
            await notifier.broadcast(nudge)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Stale task nudge failed: %s", exc)


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
        _deliver_due_daily_reminders,
        interval=DAILY_REMINDER_POLL_SECONDS,
        first=DAILY_REMINDER_POLL_SECONDS,
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
    application.job_queue.run_repeating(
        _check_new_emails, interval=EMAIL_POLL_SECONDS, first=EMAIL_POLL_SECONDS
    )
    application.job_queue.run_repeating(
        _check_k8s_health, interval=K8S_HEALTH_POLL_SECONDS, first=K8S_HEALTH_POLL_SECONDS
    )
    application.job_queue.run_repeating(
        _sample_activity, interval=ACTIVITY_SAMPLE_POLL_SECONDS, first=ACTIVITY_SAMPLE_POLL_SECONDS
    )
    application.job_queue.run_repeating(
        _check_budget_pacing, interval=BUDGET_PACING_POLL_SECONDS, first=BUDGET_PACING_POLL_SECONDS
    )
    local_tz = datetime.datetime.now().astimezone().tzinfo
    application.job_queue.run_daily(
        _send_daily_digest, time=datetime.time(hour=DAILY_DIGEST_HOUR, minute=0, tzinfo=local_tz)
    )
    application.job_queue.run_daily(
        _send_evening_expense_nudge,
        time=datetime.time(hour=EVENING_NUDGE_HOUR, minute=0, tzinfo=local_tz),
    )
    application.job_queue.run_daily(
        _send_activity_recap,
        time=datetime.time(hour=EVENING_NUDGE_HOUR, minute=15, tzinfo=local_tz),
    )
    application.job_queue.run_daily(
        _send_stale_task_nudge,
        time=datetime.time(hour=STALE_TASK_HOUR, minute=0, tzinfo=local_tz),
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
