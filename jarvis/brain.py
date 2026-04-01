"""
brain.py — LLM wrapper with rolling conversation memory.

Uses llama-cpp-python (CPU-only) to run the model directly from the GGUF file
stored in ~/.ollama/models/blobs/ — no Ollama server process required.

This bypasses the Ollama llamarunner which crashes on macOS 26 due to a Metal
framework incompatibility in MetalPerformancePrimitives.framework.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import memory
from config import (
    OLLAMA_MODEL,
    MAX_HISTORY,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GGUF model path — resolve from Ollama's local blob store
# ---------------------------------------------------------------------------

def _find_gguf() -> str:
    """Return the path to the llama3.2 GGUF blob in ~/.ollama/models/blobs/."""
    import json

    manifests_root = Path.home() / ".ollama" / "models" / "manifests"
    blobs_root = Path.home() / ".ollama" / "models" / "blobs"

    # Walk manifests to find a model whose name matches OLLAMA_MODEL
    model_name = OLLAMA_MODEL.split(":")[0].lower()
    for manifest_path in manifests_root.rglob("*"):
        if not manifest_path.is_file():
            continue
        if model_name not in str(manifest_path).lower():
            continue
        try:
            data = json.loads(manifest_path.read_text())
            for layer in data.get("layers", []):
                if "model" in layer.get("mediaType", ""):
                    digest = layer["digest"].replace(":", "-")
                    candidate = blobs_root / digest
                    if candidate.exists():
                        return str(candidate)
        except Exception:  # noqa: BLE001
            pass

    raise FileNotFoundError(
        f"Cannot find GGUF blob for model '{OLLAMA_MODEL}'. "
        f"Make sure you have run: ollama pull {OLLAMA_MODEL}"
    )


# ---------------------------------------------------------------------------
# Lazy-loaded Llama singleton
# ---------------------------------------------------------------------------

_llm = None


def _get_llm():
    """Return the loaded Llama instance, initialising it on first call."""
    global _llm
    if _llm is not None:
        return _llm

    from llama_cpp import Llama

    model_path = _find_gguf()
    logger.info("Loading model from %s …", model_path)
    _llm = Llama(
        model_path=model_path,
        n_gpu_layers=0,       # CPU-only — avoids Metal crash on macOS 26
        n_ctx=4096,           # context window
        verbose=False,
    )
    logger.info("Model loaded.")
    return _llm


# ---------------------------------------------------------------------------
# Module-level conversation history (the only intentional mutable global)
# ---------------------------------------------------------------------------

_history: list[dict[str, str]] = []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask(user_input: str) -> str:
    """Send a user message to the LLM and return its text reply.

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

    messages_with_system: list[dict[str, str]] = [
        {"role": "system", "content": effective_system},
        *_history,
    ]

    try:
        llm = _get_llm()
        response = llm.create_chat_completion(
            messages=messages_with_system,
            max_tokens=768,
            temperature=1.1,
            top_p=0.92,
            repeat_penalty=1.15,
            top_k=50,
        )
        reply: str = response["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM call failed: %s", exc)
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
