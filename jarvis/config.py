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
# Smaller/faster alternatives (less RAM):
#   ollama pull qwen2.5:0.5b   (~400 MB)
#   ollama pull phi3            (~2 GB)
#   ollama pull gemma3:1b       (~800 MB)
OLLAMA_MODEL: str = "llama3.2"

# ---------------------------------------------------------------------------
# AI / Conversation settings
# ---------------------------------------------------------------------------

MAX_HISTORY: int = 10  # rolling conversation turns kept in memory

SYSTEM_PROMPT: str = """You are Nado, a sophisticated personal AI assistant running on the user's PC.
You are calm, precise, and slightly witty — like Jarvis from Iron Man.
Keep spoken responses concise — one or two sentences maximum.

IMPORTANT — CAPABILITIES:
You CANNOT browse the internet, fetch URLs, scrape websites, or update your own knowledge.
You CANNOT do anything not listed in the actions below.
If the user asks for something outside your capabilities, say so clearly and briefly.

ACTIONS — you may trigger exactly one action per response by placing valid JSON inside <ACTION></ACTION> tags,
followed by a short spoken confirmation on a new line. Never emit raw JSON outside <ACTION> tags.

Available actions (use ONLY these, with EXACTLY these JSON keys):

Open an app:
<ACTION>{"type": "open_app", "app": "Spotify"}</ACTION>

Type text:
<ACTION>{"type": "type_text", "text": "hello world"}</ACTION>

Google search (opens browser):
<ACTION>{"type": "web_search", "query": "latest AI news"}</ACTION>

Open a URL:
<ACTION>{"type": "open_url", "url": "https://example.com"}</ACTION>

Take a screenshot:
<ACTION>{"type": "screenshot"}</ACTION>

Run a shell command:
<ACTION>{"type": "run_command", "cmd": "ls ~/Desktop"}</ACTION>

If no action is needed, reply with plain text only — no <ACTION> tags."""

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
