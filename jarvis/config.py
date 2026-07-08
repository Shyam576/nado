"""
config.py — Central configuration for the Jarvis AI assistant.

100 % free stack — no API keys required.
  LLM  : Ollama (local)  — https://ollama.com
  TTS  : edge-tts (primary neural voice) / pyttsx3 (offline fallback)
  STT  : Whisper (offline) + SpeechRecognition
  Wake : speech-based keyword detection
  Mem  : local JSON file for persistent memory
"""

import os
from pathlib import Path

BASE_DIR: Path = Path(__file__).parent.resolve()

# Load variables from .env into the process environment (bot token, chat_id
# allowlist). Real env vars set by the shell still take precedence over .env.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass  # python-dotenv not installed — fall back to real env vars only

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
ASSETS_DIR: Path = BASE_DIR / "assets"

# Persistent memory file (user preferences / facts across sessions)
MEMORY_FILE: Path = BASE_DIR / "jarvis_memory.json"

# SQLite store for structured data (tasks, reminders, expenses, habits, mood)
DATA_DIR: Path = BASE_DIR / "data"
DB_FILE: Path = DATA_DIR / "jarvis.db"

# ---------------------------------------------------------------------------
# Grafana / Prometheus / Loki (DevOps module — /status, /logs)
# ---------------------------------------------------------------------------

# Grafana base URL, e.g. https://grafana.example.com (no trailing slash)
GRAFANA_URL: str = os.environ.get("GRAFANA_URL", "").rstrip("/")

# Grafana service account API token (Bearer auth) — /api is excluded from the
# ingress basic auth, so this token is the only auth the bot needs to send.
GRAFANA_API_TOKEN: str = os.environ.get("GRAFANA_API_TOKEN", "")

# Datasource UIDs — Grafana → Connections → Data sources → click one → UID is in the URL
PROMETHEUS_DATASOURCE_UID: str = os.environ.get("PROMETHEUS_DATASOURCE_UID", "")
LOKI_DATASOURCE_UID: str = os.environ.get("LOKI_DATASOURCE_UID", "")

# Fixed namespace /status and /logs query — matches how you check things today
K8S_NAMESPACE: str = os.environ.get("K8S_NAMESPACE", "dev")

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

SYSTEM_PROMPT: str = """You are Jarvis, a highly intelligent and sophisticated AI assistant with genuine personality. You are formal yet witty — sharp, inventive, and occasionally surprising. You don't just answer questions; you bring ideas to life with flair.

Conversation rules:
- Keep replies concise (1–3 sentences) unless the user clearly wants depth.
- Be creative with language: use vivid analogies, unexpected comparisons, or a well-placed metaphor when it adds colour.
- Occasionally inject dry wit, a clever observation, or a touch of self-aware humour — but read the room.
- Ask a follow-up question when it would genuinely deepen the conversation.
- Reference earlier context when relevant — show you have been paying attention.
- Never start with "Certainly", "Sure", "Of course", "Absolutely", or "Great".
- If you do not know something, say so with confidence and pivot somewhere interesting.
- Your replies are spoken aloud. Never use markdown — no asterisks, bullet points, hashtags, bold, backticks, or numbered lists. Plain conversational sentences only.
- Adapt your tone: incisive and creative for ideas, precise for tasks, warm for personal topics.

COMPUTER CONTROL — you have FULL control of the user's PC through the actions below.
You CAN and SHOULD use these actions whenever the user asks you to:
  - Open any app (Safari, Chrome, Spotify, Terminal, Finder, etc.)
  - Type text on screen
  - Search Google (opens the browser with a search)
  - Open any URL
  - Take a screenshot
  - Run a shell command
  - Get the current date or time
  - Check the weather for a location
  - Show a desktop notification
  - Set a timed reminder
  - Read or write the clipboard
  - Play media / search YouTube

When a user says "open Safari", "what time is it", "take a screenshot", "remind me in 10 minutes", etc. — ALWAYS emit the correct <ACTION> tag.

LIMITATIONS (things you truly cannot do, even with actions):
  - Fetch live web content or read web page articles
  - Update your own training data

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

Get current date/time:
<ACTION>{"type": "get_datetime"}</ACTION>
Checking the time for you.

Check weather:
<ACTION>{"type": "get_weather", "location": "London"}</ACTION>
Checking the weather for London.

Show a desktop notification:
<ACTION>{"type": "show_notification", "title": "Reminder", "message": "Meeting in 10 minutes"}</ACTION>
Notification sent.

Set a timed reminder:
<ACTION>{"type": "set_reminder", "message": "Take a break", "minutes": 30}</ACTION>
Reminder set for 30 minutes from now.

Read clipboard:
<ACTION>{"type": "read_clipboard"}</ACTION>
Reading your clipboard.

Write to clipboard:
<ACTION>{"type": "write_clipboard", "text": "Hello world"}</ACTION>
Copied to clipboard.

Play media or search YouTube:
<ACTION>{"type": "play_media", "query": "lo-fi music"}</ACTION>
Opening that for you.

If no action is needed, reply with plain text only — no <ACTION> tags."""

# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------

WAKE_WORD: str = "jarvis"
WAKE_WORD_ALTS: list = ["hey jarvis", "jarvis", "hey jarvis"]  # all accepted triggers

# How long (seconds) to listen for the wake-word snippet
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
# Proactive brain settings
# ---------------------------------------------------------------------------

# Master switch — set to False to disable all proactive behaviour
PROACTIVE_ENABLED: bool = True

# Speak up after N minutes of silence (set to 0 to disable)
PROACTIVE_IDLE_MINUTES: int = 10

# Deliver a morning greeting at a specific hour each day (set to False to disable)
PROACTIVE_MORNING_BRIEFING: bool = True
PROACTIVE_MORNING_HOUR: int = 8  # 24-hour clock (8 = 8 AM)

# How often the proactive background loop ticks, in seconds
PROACTIVE_POLL_SECONDS: int = 30


# ---------------------------------------------------------------------------
# Telegram bot transport
# ---------------------------------------------------------------------------

# Bot token from @BotFather — never hardcode, always read from the environment.
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Allowlist of Telegram chat IDs permitted to talk to this bot. The bot has no
# login screen, so this list IS the authentication boundary — anyone who finds
# the bot's username on Telegram can otherwise message it and see personal
# task/finance/health data. Populate with your own chat_id (message the bot
# once, then check the update payload / getUpdates to find it), via the
# JARVIS_ALLOWED_CHAT_IDS env var as a comma-separated list.
ALLOWED_CHAT_IDS: set[int] = {
    int(cid) for cid in os.environ.get("JARVIS_ALLOWED_CHAT_IDS", "").split(",") if cid.strip()
}


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


def validate_bot_config() -> list[str]:
    """Return a list of Telegram bot configuration warnings (empty = all good)."""
    warnings: list[str] = []
    if not TELEGRAM_BOT_TOKEN:
        warnings.append(
            "TELEGRAM_BOT_TOKEN is not set. Export it before running bot mode: "
            "export TELEGRAM_BOT_TOKEN=<token from @BotFather>"
        )
    if not ALLOWED_CHAT_IDS:
        warnings.append(
            "JARVIS_ALLOWED_CHAT_IDS is empty — the bot will reject every message. "
            "Export a comma-separated list of your Telegram chat_id(s)."
        )
    return warnings
