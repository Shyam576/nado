"""
modules/transcription.py — Voice message transcription via Whisper.

Reuses the same Whisper model already used by the original voice.py assistant,
but lazy-loaded (like brain.py's Llama singleton) rather than eagerly at
import time — voice notes are occasional, not every bot session needs the
~74-244MB model resident in memory. Whisper decodes audio via ffmpeg
internally, so any format Telegram/Discord send (ogg/opus, webm, mp3) works
without manual conversion.
"""

import logging
from typing import Optional

from config import STT_WHISPER_MODEL

logger = logging.getLogger(__name__)

_whisper_model = None


def _get_model():
    """Return the loaded Whisper model, initialising it on first use."""
    global _whisper_model
    if _whisper_model is None:
        import whisper

        logger.info("Loading Whisper '%s' model...", STT_WHISPER_MODEL)
        _whisper_model = whisper.load_model(STT_WHISPER_MODEL)
        logger.info("Whisper model ready.")
    return _whisper_model


def transcribe_audio_file(path: str) -> Optional[str]:
    """Transcribe a local audio file to text.

    Args:
        path: Path to the audio file (any ffmpeg-decodable format).

    Returns:
        The transcribed text, or None if transcription failed or produced
        nothing (e.g. silence).
    """
    try:
        model = _get_model()
        result = model.transcribe(path, language="en", fp16=False)
        text = result.get("text", "").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Transcription failed for %s: %s", path, exc)
        return None
