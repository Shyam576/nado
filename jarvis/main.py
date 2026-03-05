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
from config import WAKE_WORD, WAKE_LISTEN_TIMEOUT, WAKE_PHRASE_LIMIT, validate_config
from voice import listen, play_acknowledgement, speak

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

    reply = ask(user_text)
    logger.debug("Raw reply: %s", reply)

    action_result, clean_reply = parse_and_execute(reply)

    if action_result:
        logger.info("Action result: %s", action_result)

    if clean_reply:
        print(f"\n[Nado]: {clean_reply}\n")
        speak(clean_reply)
    elif action_result:
        print("\n[Nado]: Done.\n")
        speak("Done.")


# ---------------------------------------------------------------------------
# Wake word detection helpers
# ---------------------------------------------------------------------------


def _contains_wake_word(text: str) -> bool:
    """Return True if the wake word appears in the transcribed text.

    Matches whole-word occurrences to avoid false positives like "tornado".

    Args:
        text: Lower-case transcription to check.

    Returns:
        True if the wake word is present.
    """
    import re
    pattern = rf"\b{re.escape(WAKE_WORD.lower())}\b"
    return bool(re.search(pattern, text))


def _strip_wake_word(text: str) -> str:
    """Remove the wake word (and optional punctuation) from the start of text.

    E.g. "nado open spotify" → "open spotify"

    Args:
        text: Lower-case text that contains the wake word.

    Returns:
        The text with the wake word prefix removed, stripped of whitespace.
    """
    import re
    cleaned = re.sub(
        rf"^\s*{re.escape(WAKE_WORD.lower())}[\s,!?.]*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Voice mode
# ---------------------------------------------------------------------------


def voice_mode() -> None:
    """Run the full voice pipeline with speech-based wake word detection.

    Continuously listens for short audio snippets, detects the wake word,
    then captures and processes the full command.  Blocks indefinitely;
    press Ctrl-C to exit.
    """
    _check_config_or_warn()

    speak("Nado online. How can I help?")
    logger.info("Wake word listening active. Say '%s' to activate.", WAKE_WORD)
    print(f"\n[Listening for wake word: '{WAKE_WORD}'] — Ctrl-C to exit\n")

    try:
        while True:
            # --- Step 1: short listen for the wake word ----------------------
            snippet = listen(
                timeout=WAKE_LISTEN_TIMEOUT,
                phrase_time_limit=WAKE_PHRASE_LIMIT,
            )

            if snippet is None:
                # Silence or error — keep looping quietly
                continue

            logger.debug("Wake check snippet: '%s'", snippet)

            if not _contains_wake_word(snippet):
                continue

            # --- Step 2: wake word detected ----------------------------------
            logger.info("Wake word detected in: '%s'", snippet)
            play_acknowledgement()

            # Check whether the command was given in the same utterance
            inline_command = _strip_wake_word(snippet)

            if inline_command:
                # e.g. user said "Nado open Spotify" in one breath
                process_input(inline_command)
            else:
                # Wake word was alone — listen for the actual command
                command = listen()
                if command:
                    process_input(command)
                else:
                    logger.info("No command heard after wake word.")

    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
        speak("Shutting down. Goodbye.")


# ---------------------------------------------------------------------------
# Text (keyboard) mode
# ---------------------------------------------------------------------------


def text_mode() -> None:
    """Run an interactive keyboard input loop for testing without a microphone.

    Type 'quit' or 'exit' to stop; type 'clear' to reset conversation history.
    """
    _check_config_or_warn()

    from brain import clear_history

    speak("Nado online. How can I help?")
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
