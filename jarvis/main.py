"""
main.py — Entry point for the Nado / JARVIS personal AI assistant.

Usage
─────
  python main.py          # full voice mode — wake word loop
  python main.py text     # keyboard input mode (for testing without a mic)

100 % free stack — no API keys required:
  LLM  : Ollama (local)           https://ollama.com
  TTS  : pyttsx3 (offline)
  STT  : SpeechRecognition + Google Web Speech (free)
  Wake : speech-based keyword detection

Voice pipeline:
  1. Listen for a short snippet of speech.
  2. Check if the transcription starts with (or contains) the wake word "nado".
  3. On match → play acknowledgement, then capture the full command.
  4. Send command to brain.py (Ollama).
  5. Parse any <ACTION> tags, execute them.
  6. Speak the cleaned reply via pyttsx3.
  7. Return to step 1.
"""

import logging
import sys
from typing import Optional

from actions import parse_and_execute
from brain import ask
from config import validate_config
import ui
from voice import listen, speak, calibrate_microphone

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
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
    reply = ask(user_text)
    logger.debug("Raw reply: %s", reply)

    action_result, clean_reply = parse_and_execute(reply)

    if action_result:
        logger.info("Action result: %s", action_result)

    if clean_reply:
        ui.add_nado(clean_reply)
        if not ui.is_active():
            print(f"\n[Nado]: {clean_reply}\n")
        speak(clean_reply)
    elif action_result:
        ui.add_nado("Done.")
        if not ui.is_active():
            print("\n[Nado]: Done.\n")
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

    ui.start()
    ui.set_state("starting")
    calibrate_microphone()

    speak("Kuzu zangpo nah-doh. I'm listening.")

    try:
        while True:
            command = listen()
            if command:
                ui.add_user(command)
                process_input(command)
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
        speak("Shutting down. Goodbye.")
    finally:
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

    speak("kuzu zangpo nah-doh. How can I help?")
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
    """Parse CLI arguments and launch the appropriate mode."""
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "voice"

    if mode == "text":
        text_mode()
    elif mode == "voice":
        voice_mode()
    else:
        print(f"Unknown mode '{mode}'. Use 'voice' (default) or 'text'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
