"""
memory.py — Persistent memory for the Jarvis assistant.

Stores user preferences, known facts, and session context across restarts
using a local JSON file (no cloud, no API keys required).

Usage
─────
  import memory

  memory.load()                      # call once at startup
  memory.set("user_name", "Alice")   # persist a fact
  memory.get("user_name")            # retrieve it later
  memory.append_fact("Alice prefers dark mode")   # freeform fact
  memory.get_context_summary()       # inject into system prompt
"""

import json
import logging
import threading
from datetime import datetime
from typing import Any, Optional

from config import MEMORY_FILE

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_memory: dict = {}

# Keys stored in the JSON file:
#   user_name     : str       - user's preferred name
#   preferences   : dict      - key/value preferences e.g. {"theme": "dark"}
#   facts         : list[str] - freeform known facts (capped at 30)
#   last_updated  : str       - ISO timestamp of last write


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_from_disk() -> None:
    global _memory
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as fh:
                _memory = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load memory file: %s. Starting fresh.", exc)
            _memory = {}
    else:
        _memory = {}


def _save_to_disk() -> None:
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(MEMORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(_memory, fh, indent=2, default=str, ensure_ascii=False)
    except OSError as exc:
        logger.error("Could not save memory: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load() -> None:
    """Load memory from disk. Call once at application startup."""
    with _lock:
        _load_from_disk()
    entry_count = len(_memory)
    logger.info("Jarvis memory loaded: %d top-level keys.", entry_count)


def get(key: str, default: Any = None) -> Any:
    """Retrieve a value from persistent memory."""
    with _lock:
        return _memory.get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001
    """Store or update a value in persistent memory and save to disk."""
    with _lock:
        _memory[key] = value
        _memory["last_updated"] = datetime.now().isoformat()
        _save_to_disk()
    logger.debug("Memory set: %s = %r", key, value)


def set_preference(key: str, value: Any) -> None:
    """Store a user preference under the 'preferences' namespace."""
    with _lock:
        if "preferences" not in _memory or not isinstance(_memory["preferences"], dict):
            _memory["preferences"] = {}
        _memory["preferences"][key] = value
        _memory["last_updated"] = datetime.now().isoformat()
        _save_to_disk()
    logger.debug("Preference saved: %s = %r", key, value)


def get_preference(key: str, default: Any = None) -> Any:
    """Retrieve a user preference."""
    with _lock:
        prefs = _memory.get("preferences", {})
        return prefs.get(key, default) if isinstance(prefs, dict) else default


def append_fact(fact: str) -> None:
    """Append a freeform fact string to the facts list (capped at 30 entries)."""
    with _lock:
        if "facts" not in _memory or not isinstance(_memory["facts"], list):
            _memory["facts"] = []
        # Avoid duplicates
        if fact not in _memory["facts"]:
            _memory["facts"].append(fact)
            # Keep only the most recent 30 facts
            if len(_memory["facts"]) > 30:
                _memory["facts"] = _memory["facts"][-30:]
        _memory["last_updated"] = datetime.now().isoformat()
        _save_to_disk()


def get_all() -> dict:
    """Return a shallow copy of the full memory dict."""
    with _lock:
        return dict(_memory)


def clear() -> None:
    """Wipe all persistent memory (irreversible)."""
    global _memory
    with _lock:
        _memory = {}
        _save_to_disk()
    logger.info("Persistent memory cleared.")


def get_context_summary() -> Optional[str]:
    """Build a short plain-text summary of stored memory for injection into the system prompt.

    Returns None if memory is empty so callers can skip injection cleanly.
    """
    with _lock:
        if not _memory:
            return None

        parts: list[str] = []

        name = _memory.get("user_name")
        if name:
            parts.append(f"The user's name is {name}.")

        prefs = _memory.get("preferences")
        if isinstance(prefs, dict) and prefs:
            for k, v in prefs.items():
                parts.append(f"User preference — {k}: {v}.")

        facts = _memory.get("facts")
        if isinstance(facts, list) and facts:
            for fact in facts[-10:]:   # inject only the most recent 10
                parts.append(fact)

    if not parts:
        return None

    return "Persistent memory about this user:\n" + "\n".join(f"  • {p}" for p in parts)
