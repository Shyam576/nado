"""
main.py — Entry point for the Jarvis AI assistant.

Usage
─────
  python main.py          # full voice mode — always-on listening
  python main.py text     # keyboard input mode (for testing without a mic)
  python main.py bot      # Telegram bot mode (see bot/telegram_bot.py)

100 % free stack — no API keys required:
  LLM  : llama-cpp-python (local, model fetched via `ollama pull`)
  TTS  : edge-tts (neural) / pyttsx3 (offline fallback)
  STT  : Whisper (offline) + SpeechRecognition
  Mem  : local JSON (jarvis_memory.json)

Voice pipeline:
  1. Load persistent memory from disk.
  2. Listen continuously for speech.
  3. Transcribe via Whisper.
  4. Send text + conversation history + system prompt → the local LLM.
  5. Parse any <ACTION> tags and execute them.
  6. Speak the cleaned reply via edge-tts / pyttsx3.
  7. Return to step 2.
"""

import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from actions import parse_and_execute
from brain import ask
import command_confirmation
from config import LOG_BACKUP_COUNT, LOG_DIR, LOG_FILE, LOG_MAX_BYTES, OWNER_ID, validate_config
import memory
import proactive
import ui
from voice import listen, speak, calibrate_microphone, _stop_event

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging(mode: str) -> None:
    """Configure root logging: always a rotating file, plus console for interactive modes.

    Bot mode runs headless under a LaunchAgent that blindly redirects
    stdout/stderr into one unbounded file (~/Library/Logs/jarvis.log, no
    rotation) — left alone, that grows forever on an always-on KeepAlive
    process. Routing through a RotatingFileHandler instead keeps disk usage
    bounded. Voice/text mode are run interactively in a terminal, so they
    keep the console handler too.

    Args:
        mode: The CLI mode ("voice", "text", or "bot") — determines whether
              a console handler is added alongside the rotating file handler.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    ]
    if mode != "bot":
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    # httpx logs every request URL at INFO — for Telegram that URL contains the
    # bot token, which must never be written to logs. WARNING keeps real errors.
    logging.getLogger("httpx").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core response pipeline
# ---------------------------------------------------------------------------


def process_input(user_text: str) -> None:
    """Send user text through the full AI + action pipeline and speak the reply.

    Args:
        user_text: The transcribed (or typed) message from the user.
    """
    logger.info("Processing: '%s'", user_text)

    ui.set_state("thinking")

    # Check for a pending run_command confirmation before the LLM ever sees
    # this utterance — a bare "yes"/"no" must resolve the parked command
    # directly, same as bot mode's intent.route() does, not get treated as
    # a fresh conversational turn.
    confirmation = command_confirmation.check(OWNER_ID, user_text)
    if confirmation is not None:
        ui.add_jarvis(confirmation)
        if not ui.is_active():
            print(f"\n[Jarvis]: {confirmation}\n")
        speak(confirmation)
        return

    reply = ask(user_text)
    logger.debug("Raw reply: %s", reply)

    action_result, clean_reply = parse_and_execute(reply)

    if action_result:
        logger.info("Action result: %s", action_result)

    if clean_reply:
        ui.add_jarvis(clean_reply)
        if not ui.is_active():
            print(f"\n[Jarvis]: {clean_reply}\n")
        speak(clean_reply)
    elif action_result:
        ui.add_jarvis("Done.")
        if not ui.is_active():
            print("\n[Jarvis]: Done.\n")
        speak("Done.")


# ---------------------------------------------------------------------------
# Voice mode  (always-on — no wake word required)
# ---------------------------------------------------------------------------


def voice_mode() -> None:
    """Run the always-on voice pipeline.

    Listens continuously and responds to every utterance — no wake word needed.
    Press Ctrl-C to exit.
    """
    _check_config_or_warn()

    _stop_event.clear()  # ensure clean state on (re-)entry

    ui.start()
    ui.set_state("starting")
    calibrate_microphone()

    speak("Jarvis online. Ready when you are.")

    proactive.start(_stop_event)

    def _sigint_handler(signum, frame):
        """Set the stop flag so the listen loop exits at its next iteration."""
        _stop_event.set()

    _prev_handler = signal.signal(signal.SIGINT, _sigint_handler)
    try:
        while not _stop_event.is_set():
            command = listen()
            if command and not _stop_event.is_set():
                proactive.record_interaction()
                ui.add_user(command)
                process_input(command)
    finally:
        signal.signal(signal.SIGINT, _prev_handler)
        _stop_event.clear()
        logger.info("Shutdown requested.")
        speak("Shutting down. Goodbye.")
        ui.stop()


# ---------------------------------------------------------------------------
# Text (keyboard) mode
# ---------------------------------------------------------------------------


def text_mode() -> None:
    """Run an interactive keyboard input loop for testing without a microphone.

    Type 'quit' or 'exit' to stop; type 'clear' to reset conversation history.
    """
    _check_config_or_warn()

    from brain import clear_history

    speak("Jarvis online. How may I assist you?")
    print("\n[Text mode] Type your message and press Enter. ('quit' to exit)\n")

    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            speak("Goodbye.")
            break

        if not user_text:
            continue

        if user_text.lower() in {"quit", "exit"}:
            speak("Goodbye.")
            break

        if user_text.lower() == "clear":
            clear_history()
            print("[Conversation history cleared.]")
            continue

        process_input(user_text)


# ---------------------------------------------------------------------------
# Bot mode — runs whichever chat transports (Telegram, Discord) are configured
# ---------------------------------------------------------------------------


async def _run_bot_transports() -> None:
    """Build and concurrently run every configured transport until interrupted."""
    import asyncio

    from config import DISCORD_BOT_TOKEN, TELEGRAM_BOT_TOKEN

    tasks_to_run = []

    if TELEGRAM_BOT_TOKEN:
        from bot.telegram_bot import build_application, run as run_telegram

        application = build_application()
        tasks_to_run.append(run_telegram(application))
    else:
        logger.info("TELEGRAM_BOT_TOKEN not set — Telegram transport disabled.")

    if DISCORD_BOT_TOKEN:
        from bot.discord_bot import build_client, run as run_discord

        client = build_client()
        tasks_to_run.append(run_discord(client))
    else:
        logger.info("DISCORD_TOKEN not set — Discord transport disabled.")

    if not tasks_to_run:
        raise RuntimeError(
            "No bot transport is configured. Set TELEGRAM_BOT_TOKEN and/or DISCORD_TOKEN in .env."
        )

    await asyncio.gather(*tasks_to_run)


def bot_mode() -> None:
    """Run Jarvis as a chat bot across every configured transport. Press Ctrl-C to exit."""
    import asyncio

    import watchdog
    from config import validate_bot_config

    for warning in validate_bot_config():
        logger.warning(warning)

    watchdog.install()

    try:
        asyncio.run(_run_bot_transports())
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")


# ---------------------------------------------------------------------------
# Config validation helper
# ---------------------------------------------------------------------------


def _check_config_or_warn() -> None:
    """Warn (but don't exit) if Ollama appears unreachable."""
    warnings = validate_config()
    for warning in warnings:
        logger.warning(warning)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments, configure logging, load persistent memory, and launch the appropriate mode."""
    from store.db import init_db

    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "voice"
    _configure_logging(mode)

    memory.load()
    init_db()

    if mode == "text":
        text_mode()
    elif mode == "voice":
        voice_mode()
    elif mode == "bot":
        bot_mode()
    else:
        print(f"Unknown mode '{mode}'. Use 'voice' (default), 'text', or 'bot'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
