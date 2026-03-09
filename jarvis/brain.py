"""
brain.py — Ollama LLM wrapper with rolling conversation memory.

Maintains the last MAX_HISTORY turns of the conversation and sends them with
every request, giving Jarvis context-awareness across a session.  Persistent
user preferences and facts are loaded from memory.py and injected into every
system prompt.

Requires Ollama running locally:  https://ollama.com
  1. Install Ollama
  2. ollama pull llama3.2
  3. ollama serve          (or it auto-starts on macOS after install)
"""

import logging
from typing import Optional

import ollama

import memory
from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    MAX_HISTORY,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level conversation history (the only intentional mutable global)
# ---------------------------------------------------------------------------

_history: list[dict[str, str]] = []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask(user_input: str) -> str:
    """Send a user message to Ollama and return its text reply.

    Conversation history is automatically maintained.  The oldest turns are
    pruned once the history exceeds MAX_HISTORY * 2 message objects (each
    turn is a user + assistant pair).

    Args:
        user_input: The transcribed or typed message from the user.

    Returns:
        The model's raw reply string (may contain <ACTION> tags).
    """
    global _history

    _history.append({"role": "user", "content": user_input})

    # Build system prompt — append persistent memory context if available
    mem_context = memory.get_context_summary()
    effective_system = SYSTEM_PROMPT
    if mem_context:
        effective_system = SYSTEM_PROMPT + "\n\n" + mem_context

    # Prepend the system prompt as a system-role message
    messages_with_system: list[dict[str, str]] = [
        {"role": "system", "content": effective_system},
        *_history,
    ]

    try:
        client = ollama.Client(host=OLLAMA_BASE_URL)
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=messages_with_system,
            options={"num_predict": 512, "temperature": 0.85},
        )
        reply: str = response["message"]["content"]
    except ollama.ResponseError as exc:
        logger.error("Ollama response error: %s", exc)
        if "model" in str(exc).lower():
            reply = (
                f"I can't find the model '{OLLAMA_MODEL}'. "
                f"Run: ollama pull {OLLAMA_MODEL}"
            )
        else:
            reply = "I received an unexpected response from my local brain. Most peculiar."
    except ConnectionRefusedError:
        logger.error("Cannot connect to Ollama at %s", OLLAMA_BASE_URL)
        reply = (
            "I can't reach my local brain. "
            "Is Ollama running? Try: ollama serve"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error calling Ollama: %s", exc)
        reply = "Something went wrong on my end. Do give me a moment and try again."

    _history.append({"role": "assistant", "content": reply})

    # Prune to keep only the last MAX_HISTORY turns (2 messages per turn)
    max_messages = MAX_HISTORY * 2
    if len(_history) > max_messages:
        _history = _history[-max_messages:]

    return reply


def clear_history() -> None:
    """Wipe the conversation history, starting a fresh session."""
    global _history
    _history = []
    logger.info("Conversation history cleared.")


def get_history() -> list[dict[str, str]]:
    """Return a copy of the current conversation history.

    Returns:
        A shallow copy of the history list for inspection without mutation.
    """
    return list(_history)


def history_summary() -> str:
    """Return a one-line string describing the current history depth.

    Returns:
        E.g. "3 turns in memory (6 messages)."
    """
    turns = len(_history) // 2
    msgs = len(_history)
    return f"{turns} turn{'s' if turns != 1 else ''} in memory ({msgs} messages)."


def inject_system_context(context: str) -> None:
    """Prepend extra context as an assistant message without a user turn.

    Useful for injecting real-time data (e.g. current time, clipboard content)
    before the next user query.

    Args:
        context: A plain-text string to inject.
    """
    _history.append({"role": "assistant", "content": context})
    logger.debug("Injected system context: %.80s…", context)
