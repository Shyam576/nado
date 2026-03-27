"""
proactive.py — Proactive brain for the Jarvis assistant.

Runs a background daemon thread that monitors time and user-idle state, then
initiates conversation unprompted via a one-shot Ollama call.

Triggers (all configurable in config.py):
  • Morning briefing  — spoken once per day at PROACTIVE_MORNING_HOUR.
  • Idle check-in     — spoken after PROACTIVE_IDLE_MINUTES of silence.

Design notes:
  - Uses a separate Ollama call so no fake "user" turn pollutes chat history.
  - The proactive reply is injected into brain history as an assistant message
    so future responses are aware of what Jarvis already said.
  - All triggers are skipped while Jarvis is already speaking / thinking.
  - Call record_interaction() every time the user speaks to reset the idle timer.
"""

import datetime
import logging
import threading
import time
from typing import Optional

import ollama

import brain
import memory
import ui
from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    PROACTIVE_ENABLED,
    PROACTIVE_IDLE_MINUTES,
    PROACTIVE_MORNING_BRIEFING,
    PROACTIVE_MORNING_HOUR,
    PROACTIVE_POLL_SECONDS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_last_interaction: float = time.time()
_morning_briefed_date: Optional[datetime.date] = None
_state_lock = threading.Lock()

# ---------------------------------------------------------------------------
# System prompt for proactive (one-shot) calls
# ---------------------------------------------------------------------------

_PROACTIVE_SYSTEM = (
    "You are Jarvis, a sophisticated AI assistant speaking to the user UNPROMPTED "
    "— the user has not said anything. Generate one or two short, natural spoken "
    "sentences only. No markdown. No lists. Be warm but not intrusive. "
    "Never begin with 'Certainly', 'Sure', 'Of course', 'Absolutely', or 'Great'."
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_interaction() -> None:
    """Reset the idle timer. Call this every time the user speaks."""
    global _last_interaction
    with _state_lock:
        _last_interaction = time.time()


def start(stop_event: threading.Event) -> None:
    """Start the proactive background thread.

    Args:
        stop_event: The same threading.Event used by voice_mode to signal shutdown.
    """
    if not PROACTIVE_ENABLED:
        logger.info("Proactive brain is disabled.")
        return

    t = threading.Thread(
        target=_loop,
        args=(stop_event,),
        daemon=True,
        name="proactive-brain",
    )
    t.start()
    logger.info("Proactive brain started (idle=%dm, morning=%s hour=%d).",
                PROACTIVE_IDLE_MINUTES, PROACTIVE_MORNING_BRIEFING, PROACTIVE_MORNING_HOUR)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_busy() -> bool:
    """Return True while Jarvis is speaking, thinking, or transcribing."""
    return ui._state in ("speaking", "thinking", "transcribing", "starting")


def _ollama_call(prompt: str) -> Optional[str]:
    """Fire a lightweight one-shot Ollama chat call for proactive messages.

    Does NOT touch the main conversation history in brain.py.

    Args:
        prompt: The synthesised instruction for what to say.

    Returns:
        The model's reply string, or None on failure.
    """
    mem_context = memory.get_context_summary()
    system = _PROACTIVE_SYSTEM
    if mem_context:
        system = system + "\n\n" + mem_context

    try:
        client = ollama.Client(host=OLLAMA_BASE_URL)
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            options={
                "num_predict": 120,
                "temperature": 1.0,
                "top_p": 0.9,
                "num_gpu": 0,
            },
        )
        return response["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.error("Proactive Ollama call failed: %s", exc)
        return None


def _deliver(text: str) -> None:
    """Show text in the UI, inject into brain history, and speak it aloud.

    Importing speak here (not at module top) avoids a circular import since
    voice.py imports from config, and proactive.py is imported by main.py which
    also imports voice.py.
    """
    from voice import speak  # local import to sidestep circular dependency

    ui.add_jarvis(text)
    brain.inject_system_context(f"[Jarvis said proactively]: {text}")
    speak(text)


def _check_morning_briefing(now: datetime.datetime) -> bool:
    """Fire the morning greeting once per day at the configured hour.

    Args:
        now: The current datetime.

    Returns:
        True if the briefing was delivered, False otherwise.
    """
    global _morning_briefed_date

    if not PROACTIVE_MORNING_BRIEFING:
        return False
    if now.hour != PROACTIVE_MORNING_HOUR:
        return False

    today = now.date()
    with _state_lock:
        if _morning_briefed_date == today:
            return False
        _morning_briefed_date = today  # mark before the LLM call so we don't double-fire

    prompt = (
        f"It is {now.strftime('%A, %B %-d')} at {now.strftime('%-I:%M %p')}. "
        "Give the user a concise, warm good-morning greeting and briefly offer to help with their day."
    )
    reply = _ollama_call(prompt)
    if reply:
        logger.info("Proactive: morning briefing → %s", reply[:60])
        _deliver(reply)
    return True


def _check_idle(now: datetime.datetime) -> bool:  # noqa: ARG001
    """Check in after the user has been idle for PROACTIVE_IDLE_MINUTES.

    Args:
        now: The current datetime (unused but kept for consistent signature).

    Returns:
        True if a check-in was delivered, False otherwise.
    """
    if PROACTIVE_IDLE_MINUTES <= 0:
        return False

    with _state_lock:
        idle_seconds = time.time() - _last_interaction

    if idle_seconds < PROACTIVE_IDLE_MINUTES * 60:
        return False

    # Reset the timer immediately so we don't spam on every subsequent poll
    with _state_lock:
        _last_interaction = time.time()

    idle_minutes = int(idle_seconds // 60)
    prompt = (
        f"The user has been quiet for about {idle_minutes} minute"
        f"{'s' if idle_minutes != 1 else ''}. "
        "Gently check if they need anything or offer a brief, relevant suggestion."
    )
    reply = _ollama_call(prompt)
    if reply:
        logger.info("Proactive: idle check-in → %s", reply[:60])
        _deliver(reply)
    return True


def _loop(stop_event: threading.Event) -> None:
    """Main proactive background loop.

    Waits 60 s on startup (so the welcome greeting finishes), then polls
    every PROACTIVE_POLL_SECONDS seconds until stop_event is set.
    """
    # Give Jarvis time to finish startup greeting
    stop_event.wait(timeout=60)

    while not stop_event.is_set():
        try:
            if not _is_busy():
                now = datetime.datetime.now()
                # Morning briefing takes priority; idle check only if no briefing fired
                if not _check_morning_briefing(now):
                    _check_idle(now)
        except Exception as exc:  # noqa: BLE001
            logger.error("Proactive loop error: %s", exc)

        stop_event.wait(timeout=PROACTIVE_POLL_SECONDS)
