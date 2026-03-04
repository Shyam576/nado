"""
voice.py — Speech-to-Text (STT) and Text-to-Speech (TTS) for the Nado assistant.

STT : SpeechRecognition library with Google Web Speech API (free, no key needed).
TTS : pyttsx3 — fully offline, uses the OS's built-in speech engine.
      macOS   → uses NSSpeechSynthesizer (many voices available in System Settings)
      Windows → uses SAPI5
      Linux   → uses eSpeak

No API keys required.
"""

import logging
import threading
from typing import Optional

import pyttsx3
import speech_recognition as sr

from config import (
    STT_PHRASE_LIMIT,
    STT_TIMEOUT,
    TTS_RATE,
    TTS_VOLUME,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pyttsx3 TTS engine — initialised once, reused across calls
# ---------------------------------------------------------------------------

_tts_engine: Optional[pyttsx3.Engine] = None
_tts_lock = threading.Lock()  # pyttsx3 is not thread-safe


def _get_tts_engine() -> pyttsx3.Engine:
    """Lazily initialise and return the pyttsx3 engine (singleton).

    Returns:
        The configured pyttsx3 engine instance.
    """
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = pyttsx3.init()
        _tts_engine.setProperty("rate", TTS_RATE)
        _tts_engine.setProperty("volume", TTS_VOLUME)

        # Try to pick a pleasant voice — prefer a female voice on macOS/Windows
        voices = _tts_engine.getProperty("voices")
        preferred = None
        for voice in voices:
            name = (voice.name or "").lower()
            if any(k in name for k in ("samantha", "zira", "hazel", "victoria", "karen")):
                preferred = voice.id
                break
        if preferred:
            _tts_engine.setProperty("voice", preferred)
            logger.debug("TTS voice set to: %s", preferred)
        else:
            logger.debug("Using default TTS voice.")

    return _tts_engine


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


def speak(text: str) -> None:
    """Convert text to speech and play it through the default audio output.

    Uses pyttsx3 (offline, no API key needed).

    Args:
        text: The string to be spoken aloud.
    """
    if not text:
        return

    logger.debug("Speaking: %.80s…", text)

    try:
        with _tts_lock:
            engine = _get_tts_engine()
            engine.say(text)
            engine.runAndWait()
    except Exception as exc:  # noqa: BLE001
        logger.error("TTS failed: %s — printing to console.", exc)
        print(f"\n[Nado]: {text}\n")


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------

_recogniser = sr.Recognizer()
_recogniser.energy_threshold = 300
_recogniser.dynamic_energy_threshold = True
_recogniser.pause_threshold = 0.8


def listen(
    timeout: int = STT_TIMEOUT,
    phrase_time_limit: int = STT_PHRASE_LIMIT,
) -> Optional[str]:
    """Record a single utterance from the microphone and transcribe it.

    Uses Google Web Speech API (free — no API key required).

    Args:
        timeout: Seconds to wait before giving up if no speech starts.
        phrase_time_limit: Maximum recording duration in seconds.

    Returns:
        The transcribed string in lower-case, or ``None`` on failure.
    """
    try:
        with sr.Microphone() as source:
            logger.debug("Adjusting for ambient noise…")
            _recogniser.adjust_for_ambient_noise(source, duration=0.3)
            logger.info("Listening…")
            audio = _recogniser.listen(
                source,
                timeout=timeout,
                phrase_time_limit=phrase_time_limit,
            )
    except sr.WaitTimeoutError:
        logger.debug("Listen timed out — no speech detected.")
        return None
    except OSError as exc:
        logger.error("Microphone error: %s", exc)
        return None

    try:
        text: str = _recogniser.recognize_google(audio)
        logger.info("Recognised: '%s'", text)
        return text.lower()
    except sr.UnknownValueError:
        logger.debug("Could not understand speech.")
        return None
    except sr.RequestError as exc:
        logger.error("Google STT request failed: %s", exc)
        return None


def play_acknowledgement() -> None:
    """Speak a short acknowledgement when the wake word is detected."""
    speak("Yes?")

