"""
voice.py — Speech-to-Text (STT) and Text-to-Speech (TTS) for the Nado assistant.

STT : SpeechRecognition + Whisper (local, no API key).
TTS : edge-tts (primary) — Microsoft neural voices, free, no API key, human-sounding.
      Falls back to pyttsx3 automatically when offline.
      macOS playback via afplay (built-in), Windows via PowerShell, Linux via mpg123.

No API keys required.
"""

import asyncio
import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import wave
from math import gcd
from typing import Optional

import edge_tts
import numpy as np
import pyttsx3
import speech_recognition as sr
import whisper
from scipy.signal import resample_poly

from config import (
    EDGE_TTS_VOICE,
    STT_PHRASE_LIMIT,
    STT_TIMEOUT,
    STT_WHISPER_MODEL,
    TTS_RATE,
    TTS_VOLUME,
    TTS_VOICE,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whisper STT model — loaded once at import time
# ---------------------------------------------------------------------------

logger.info("Loading Whisper '%s' model…", STT_WHISPER_MODEL)
_whisper_model = whisper.load_model(STT_WHISPER_MODEL)
logger.info("Whisper model ready.")

# Whisper was trained on 16 kHz audio — resample to match
_WHISPER_SAMPLE_RATE = 16000

# Short phrases Whisper hallucinates when audio is silence/noise — discard them
_WHISPER_HALLUCINATIONS = {
    "thank you.", "thanks.", "you.", ".", "the.", "bye.", "bye-bye.",
    "please subscribe.", "thanks for watching.", "you", "thank you",
    "thank you very much.", "thanks a lot.",
}

# Primes Whisper to expect conversational Indian-accented English
_WHISPER_INITIAL_PROMPT = (
    "Transcribe this spoken English conversation accurately. "
    "The speaker may have an Indian accent."
)

# ---------------------------------------------------------------------------
# pyttsx3 TTS engine — fresh instance created per speak() call (macOS fix)
# ---------------------------------------------------------------------------

_tts_lock = threading.Lock()


def _make_tts_engine() -> pyttsx3.Engine:
    """Create and configure a fresh pyttsx3 engine instance.

    Returns:
        A newly initialised, configured pyttsx3 engine.
    """
    engine = pyttsx3.init()
    engine.setProperty("rate", TTS_RATE)
    engine.setProperty("volume", TTS_VOLUME)

    if TTS_VOICE:
        engine.setProperty("voice", TTS_VOICE)
        logger.debug("TTS voice set to: %s", TTS_VOICE)
    else:
        voices = engine.getProperty("voices")
        preferred = None
        for voice in voices:
            name = (voice.name or "").lower()
            if any(k in name for k in ("samantha", "zira", "hazel", "victoria", "karen")):
                preferred = voice.id
                break
        if preferred:
            engine.setProperty("voice", preferred)
            logger.debug("TTS voice auto-selected: %s", preferred)

    return engine


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


async def _edge_speak_async(text: str) -> None:
    """Generate speech with edge-tts and play it via the OS audio player."""
    communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    try:
        await communicate.save(tmp_path)
        if sys.platform == "darwin":
            subprocess.run(["afplay", tmp_path], check=True)
        elif sys.platform == "win32":
            subprocess.run(["powershell", "-c", f'(New-Object Media.SoundPlayer "{tmp_path}").PlaySync()'], check=True)
        else:
            # Linux — install mpg123: sudo apt install mpg123
            subprocess.run(["mpg123", "-q", tmp_path], check=True)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _pyttsx3_speak(text: str) -> None:
    """Fallback TTS using pyttsx3 (offline, robotic but always works)."""
    with _tts_lock:
        engine = _make_tts_engine()
        engine.say(text)
        engine.runAndWait()
        engine.stop()


def speak(text: str) -> None:
    """Convert text to speech using edge-tts (Microsoft neural voices).

    Falls back to pyttsx3 automatically if edge-tts fails (e.g. no internet).

    Args:
        text: The string to be spoken aloud.
    """
    if not text:
        return

    logger.debug("Speaking: %.80s…", text)

    try:
        asyncio.run(_edge_speak_async(text))
    except Exception as exc:  # noqa: BLE001
        logger.warning("edge-tts failed (%s); falling back to pyttsx3.", exc)
        try:
            _pyttsx3_speak(text)
        except Exception as exc2:  # noqa: BLE001
            logger.error("TTS fallback also failed: %s", exc2)
            print(f"\n[Nado]: {text}\n")


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------

_recogniser = sr.Recognizer()
_recogniser.energy_threshold = 300
_recogniser.dynamic_energy_threshold = True
_recogniser.pause_threshold = 1.5       # wait 1.5s of silence before ending phrase
_recogniser.non_speaking_duration = 1.0 # how much silence to keep at end of phrase


def calibrate_microphone() -> None:
    """Run a one-time ambient noise calibration so wake-word detection is accurate."""
    try:
        with sr.Microphone() as source:
            logger.info("Calibrating microphone for ambient noise (1 s)…")
            _recogniser.adjust_for_ambient_noise(source, duration=1.0)
            logger.info("Calibration done. Energy threshold: %.0f", _recogniser.energy_threshold)
    except OSError as exc:
        logger.error("Microphone calibration failed: %s", exc)


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
        # Convert raw audio to float32 numpy array
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(audio.sample_width)
            wf.setframerate(audio.sample_rate)
            wf.writeframes(audio.get_raw_data())
        wav_buffer.seek(0)

        audio_np = np.frombuffer(wav_buffer.read(), dtype=np.int16).astype(np.float32) / 32768.0

        # Resample to 16 kHz if needed (Whisper was trained at 16 kHz)
        src_rate = audio.sample_rate
        if src_rate != _WHISPER_SAMPLE_RATE:
            common = gcd(src_rate, _WHISPER_SAMPLE_RATE)
            audio_np = resample_poly(audio_np, _WHISPER_SAMPLE_RATE // common, src_rate // common)

        result = _whisper_model.transcribe(
            audio_np.astype(np.float32),
            language="en",
            fp16=False,
            initial_prompt=_WHISPER_INITIAL_PROMPT,
            condition_on_previous_text=False,
        )
        text: str = result["text"].strip()

        # Filter out common Whisper hallucinations on silence/noise
        if not text or text.lower() in _WHISPER_HALLUCINATIONS:
            return None

        logger.info("Recognised: '%s'", text)
        return text.lower()
    except Exception as exc:  # noqa: BLE001
        logger.error("Whisper transcription failed: %s", exc)
        return None


def play_acknowledgement() -> None:
    """Speak a short acknowledgement when the wake word is detected."""
    speak("Yes?")

