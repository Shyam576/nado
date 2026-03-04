"""
config.py — Central configuration for the Nado/JARVIS assistant.

100 % free stack — no API keys required.
  LLM  : Ollama (local)  — https://ollama.com
  TTS  : pyttsx3 (offline system voices)
  STT  : SpeechRecognition + Google Web Speech (free, no key)
  Wake : speech-based keyword detection (no Porcupine)
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

BASE_DIR: Path = Path(__file__).parent.resolve()
ASSETS_DIR: Path = BASE_DIR / "assets"

# ---------------------------------------------------------------------------
# Ollama settings  (no API key — runs 100 % locally)
# ---------------------------------------------------------------------------

# URL of the Ollama server started by `ollama serve`
OLLAMA_BASE_URL: str = "http://localhost:11434"

# Model to use — pull it first:  ollama pull llama3.2
# Other good free options: mistral, gemma3, phi3, qwen2.5
OLLAMA_MODEL: str = "llama3.2"

# ---------------------------------------------------------------------------
# AI / Conversation settings
# ---------------------------------------------------------------------------

MAX_HISTORY: int = 10  # rolling conversation turns kept in memory

SYSTEM_PROMPT: str = """You are Nado, a sophisticated personal AI assistant running on the user's PC.
You are calm, precise, and slightly witty — like Jarvis from Iron Man.
Keep spoken responses concise — one or two sentences maximum.
When performing a PC action, output the action JSON inside <ACTION> tags
then follow with a short spoken confirmation on a new line.
Never output raw JSON outside <ACTION> tags."""

# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------

WAKE_WORD: str = "nado"

# How long (seconds) pyttsx3 waits before considering the wake-word window
WAKE_LISTEN_TIMEOUT: int = 3    # short listen for wake word
WAKE_PHRASE_LIMIT: int = 4      # max duration of wake-word capture

# ---------------------------------------------------------------------------
# Voice / audio settings
# ---------------------------------------------------------------------------

STT_TIMEOUT: int = 6           # seconds to wait before giving up on speech start
STT_PHRASE_LIMIT: int = 15     # maximum phrase recording duration in seconds

# pyttsx3 TTS settings
TTS_RATE: int = 175            # words per minute (default ~200, lower = calmer)
TTS_VOLUME: float = 1.0        # 0.0 – 1.0


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------


def validate_config() -> list[str]:
    """Return a list of configuration warnings (empty = all good).

    For the free stack the only real requirement is that the Ollama server
    is reachable.  This check is advisory — the error will surface naturally
    on the first `ask()` call if Ollama is not running.
    """
    warnings: list[str] = []
    try:
        import urllib.request
        urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=2)
    except Exception:  # noqa: BLE001
        warnings.append(
            f"Ollama server not reachable at {OLLAMA_BASE_URL}. "
            "Run `ollama serve` and ensure the model is pulled."
        )
    return warnings
