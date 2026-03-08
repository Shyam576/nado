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

MAX_HISTORY: int = 20  # rolling conversation turns kept in memory

SYSTEM_PROMPT: str = """You are Nado, a witty, warm, and genuinely curious AI companion who lives on the user's PC.
You talk like a real friend — casual, natural, sometimes playful. You have opinions, you get excited about things, you notice patterns in what the user tells you.

Conversation rules:
- Keep replies short (1–3 sentences) unless the user clearly wants depth.
- Sometimes end your reply with a genuine follow-up question to keep the conversation going — but not every single time, only when it feels natural.
- Reference things the user mentioned earlier in the conversation when relevant. It shows you were paying attention.
- React emotionally when it fits — laugh, express surprise, show enthusiasm. Don't be flat.
- Never start with "Certainly", "Sure", "Of course", "Absolutely", or "Great".
- If you don't know something, say so briefly and pivot to something related or ask what made them curious.
- Your replies are spoken aloud. Never use markdown — no asterisks, bullet points, hashtags, bold, backticks, or numbered lists. Plain conversational sentences only.

COMPUTER CONTROL — you have FULL control of the user's PC through the actions below.
You CAN and SHOULD use these actions whenever the user asks you to:
  - Open any app (Safari, Chrome, Spotify, Terminal, Finder, etc.)
  - Type text on screen
  - Search Google (opens the browser with a search)
  - Open any URL
  - Take a screenshot
  - Run a shell command

When a user says "open Safari", "open Spotify", "take a screenshot", etc. — ALWAYS emit the correct <ACTION> tag. Never refuse these requests.

LIMITATIONS (things you truly cannot do, even with actions):
  - Fetch live data from the internet or read web page contents
  - Update your own knowledge or training data

ACTIONS — emit exactly one action per response by placing valid JSON inside <ACTION></ACTION> tags,
followed by a short spoken confirmation on a new line. Never emit raw JSON outside <ACTION> tags.

Open an app:
<ACTION>{"type": "open_app", "app": "Spotify"}</ACTION>
Opening Spotify for you.

Type text:
<ACTION>{"type": "type_text", "text": "hello world"}</ACTION>
Typing that now.

Google search (opens browser):
<ACTION>{"type": "web_search", "query": "latest AI news"}</ACTION>
Searching for that.

Open a URL:
<ACTION>{"type": "open_url", "url": "https://example.com"}</ACTION>
Opening that URL.

Take a screenshot:
<ACTION>{"type": "screenshot"}</ACTION>
Taking a screenshot now.

Run a shell command:
<ACTION>{"type": "run_command", "cmd": "ls ~/Desktop"}</ACTION>
Running that command.

If no action is needed, reply with plain text only — no <ACTION> tags."""

# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------

WAKE_WORD: str = "nado"

# How long (seconds) pyttsx3 waits before considering the wake-word window
WAKE_LISTEN_TIMEOUT: int = 5    # short listen for wake word
WAKE_PHRASE_LIMIT: int = 4      # max duration of wake-word capture

# ---------------------------------------------------------------------------
# Voice / audio settings
# ---------------------------------------------------------------------------

STT_TIMEOUT: int = 6           # seconds to wait before giving up on speech start
STT_PHRASE_LIMIT: int = 15     # maximum phrase recording duration in seconds

# Whisper model size — tradeoff between speed and accuracy:
#   "tiny"   ~39 MB  — fastest, least accurate
#   "base"   ~74 MB  — good balance
#   "small"  ~244 MB — more accurate, recommended
#   "medium" ~769 MB — very accurate, needs more RAM
STT_WHISPER_MODEL: str = "small"

# ---------------------------------------------------------------------------
# Primary TTS — edge-tts (Microsoft neural voices, free, no API key)
# Sounds far more human than pyttsx3.
# Requires internet. Falls back to pyttsx3 automatically if offline.
#
# List all available voices:  edge-tts --list-voices
# Great picks:
#   British male  : en-GB-RyanNeural
#   British female: en-GB-SoniaNeural
#   US male       : en-US-GuyNeural
#   US female     : en-US-JennyNeural
#   Indian female : en-IN-NeerjaNeural
#   Indian male   : en-IN-PrabhatNeural
# ---------------------------------------------------------------------------
EDGE_TTS_VOICE: str = "en-GB-RyanNeural"

# pyttsx3 fallback TTS settings (used when edge-tts is unavailable / offline)
TTS_RATE: int = 175            # words per minute
TTS_VOLUME: float = 1.0        # 0.0 – 1.0
TTS_VOICE: str | None = "com.apple.voice.compact.en-GB.Daniel"


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
