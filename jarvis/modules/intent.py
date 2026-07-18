"""
modules/intent.py — Natural-language intent routing.

Sits between the slash-command dispatcher and the LLM chat fallback: plain
text like "spent 300 on momos" or "remind me in 2 hours to call mom" gets
mapped onto the same module functions the slash commands call, so nothing
needs to be memorised.

Routing strategy (cheapest first):
  1. Fast-path regexes for the two highest-traffic patterns (expenses,
     reminders) — deterministic, no LLM latency.
  2. One-shot LLM classification into a small fixed intent set, constrained
     to JSON output (llama.cpp grammar mode) at temperature 0.
  3. Anything unclassifiable returns None and the caller falls through to
     brain.ask() exactly as before — chat behaviour is unchanged.

The classification call deliberately does NOT touch brain._history (same
pattern as proactive.py's one-shot calls).
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from modules import digest, expenses, finance, tasks

logger = logging.getLogger(__name__)


@dataclass
class IntentReply:
    """The result of a handled intent.

    Attributes:
        text: The reply text to send back to the user.
        image_path: Optional path to an image (e.g. a screenshot) the
                    transport should attach alongside the text.
    """

    text: str
    image_path: Optional[str] = None

# ---------------------------------------------------------------------------
# Fast-path regexes — deterministic, no LLM round-trip
# ---------------------------------------------------------------------------

# "spent 300 on momos" / "paid 1,200 for taxi" / "300 on lunch"
_EXPENSE_RE = re.compile(
    r"^(?:i\s+)?(?:spent|paid|bought)\s+(?:nu\.?\s*|btn\s*)?([\d,]+(?:\.\d+)?)\s*"
    r"(?:nu\.?|btn)?\s*(?:on|for)?\s*(.*)$",
    re.IGNORECASE,
)

# "remind me in 30 minutes to take a break" / "remind me in 2 hours call mom"
_REMINDER_RE = re.compile(
    r"^remind\s+me\s+in\s+(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h)\s+(?:to\s+)?(.+)$",
    re.IGNORECASE,
)

# "screenshot" / "take a screenshot" / "grab a screenshot of my screen"
_SCREENSHOT_RE = re.compile(
    r"^(?:take|grab|capture)?\s*a?\s*screen\s*shot(?:\s+of\s+(?:my\s+)?screen)?$",
    re.IGNORECASE,
)


def _fast_path(chat_id: str, text: str) -> Optional[IntentReply]:
    """Handle unambiguous high-traffic phrasings without an LLM call."""
    match = _EXPENSE_RE.match(text)
    if match:
        amount, description = match.group(1).replace(",", ""), match.group(2).strip()
        args = [amount] + (description.split() if description else [])
        return IntentReply(expenses.add_expense_from_text(chat_id, args))

    match = _REMINDER_RE.match(text)
    if match:
        quantity, unit, message = int(match.group(1)), match.group(2).lower(), match.group(3)
        minutes = quantity * 60 if unit.startswith(("h",)) else quantity
        return IntentReply(tasks.add_reminder(chat_id, [str(minutes)] + message.split()))

    if _SCREENSHOT_RE.match(text):
        return _dispatch_screenshot(chat_id, {})

    return None


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM = """You classify one user message into an intent. Reply with ONLY a JSON object.

Intents and their required fields:
  {"intent": "add_expense", "amount": <number>, "description": "<what it was for>"}
  {"intent": "add_task", "title": "<task title>"}
  {"intent": "list_tasks"}
  {"intent": "complete_task", "task_id": <number>}
  {"intent": "set_reminder", "minutes": <number>, "message": "<reminder text>"}
  {"intent": "budget_status"}
  {"intent": "list_expenses"}
  {"intent": "daily_summary"}
  {"intent": "gold_price"}
  {"intent": "ter_price"}
  {"intent": "take_screenshot"}
  {"intent": "open_app", "app": "<application name>"}
  {"intent": "run_command", "command": "<shell command>"}
  {"intent": "read_clipboard"}
  {"intent": "write_clipboard", "text": "<text to copy>"}
  {"intent": "play_media", "query": "<what to play>"}
  {"intent": "get_weather", "location": "<place>"}
  {"intent": "chat"}

Rules:
- "chat" is for greetings, questions, opinions, and anything that is not one of the actions above.
- Only pick an action intent when the message clearly asks for that action.
- Convert hours to minutes for reminders.
- Never invent an amount, task id, or reminder time that is not in the message.
- The laptop actions (screenshot, open_app, run_command, clipboard, play_media) run on the user's own computer.
- In run_command, always write paths as absolute or ~-relative (e.g. ~/Desktop), never bare relative names.

Examples:
"spent 250 on coffee" -> {"intent": "add_expense", "amount": 250, "description": "coffee"}
"how much have I spent this month" -> {"intent": "budget_status"}
"show my recent expenses" -> {"intent": "list_expenses"}
"add a task to renew my passport" -> {"intent": "add_task", "title": "renew my passport"}
"what's on my plate today" -> {"intent": "daily_summary"}
"mark task 3 as done" -> {"intent": "complete_task", "task_id": 3}
"finished task 3" -> {"intent": "complete_task", "task_id": 3}
"remind me in an hour to stretch" -> {"intent": "set_reminder", "minutes": 60, "message": "stretch"}
"what's the gold rate" -> {"intent": "gold_price"}
"open spotify" -> {"intent": "open_app", "app": "Spotify"}
"show me what's on my screen" -> {"intent": "take_screenshot"}
"run git status in ~/Desktop/nado/jarvis" -> {"intent": "run_command", "command": "cd ~/Desktop/nado/jarvis && git status"}
"what's in my clipboard" -> {"intent": "read_clipboard"}
"copy my email s@example.com to the clipboard" -> {"intent": "write_clipboard", "text": "s@example.com"}
"play some lo-fi music" -> {"intent": "play_media", "query": "lo-fi music"}
"what's the weather in thimphu" -> {"intent": "get_weather", "location": "Thimphu"}
"how are you doing" -> {"intent": "chat"}
"what do you think about the weather" -> {"intent": "chat"}"""


def _classify(text: str) -> Optional[dict]:
    """One-shot LLM intent classification. Returns a parsed dict or None.

    Uses the shared Llama singleton from brain.py without touching the chat
    history, and constrains output to valid JSON via llama.cpp's grammar
    support (response_format json_object).
    """
    import brain  # local import to avoid loading the model at module import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": text},
            ],
            max_tokens=96,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Intent classification failed (%s) — falling back to chat.", exc)
        return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch_add_expense(chat_id: str, data: dict) -> Optional[IntentReply]:
    amount = data.get("amount")
    if not isinstance(amount, (int, float)) or amount <= 0:
        return None
    description = str(data.get("description", "")).strip()
    return IntentReply(expenses.add_expense_from_text(chat_id, [str(amount)] + description.split()))


def _dispatch_add_task(chat_id: str, data: dict) -> Optional[IntentReply]:
    title = str(data.get("title", "")).strip()
    if not title:
        return None
    return IntentReply(tasks.add_task(chat_id, title.split()))


def _dispatch_complete_task(chat_id: str, data: dict) -> Optional[IntentReply]:
    task_id = data.get("task_id")
    if not isinstance(task_id, int) or task_id <= 0:
        return None
    return IntentReply(tasks.complete_task(chat_id, [str(task_id)]))


def _dispatch_set_reminder(chat_id: str, data: dict) -> Optional[IntentReply]:
    minutes = data.get("minutes")
    message = str(data.get("message", "")).strip()
    if not isinstance(minutes, int) or minutes <= 0 or not message:
        return None
    return IntentReply(tasks.add_reminder(chat_id, [str(minutes)] + message.split()))


# --- Laptop actions (actions.py imported lazily — it pulls in pyautogui) ---


def _dispatch_screenshot(chat_id: str, data: dict) -> Optional[IntentReply]:
    import actions

    path = actions.take_screenshot()
    return IntentReply("Here's your screen.", image_path=path)


def _dispatch_open_app(chat_id: str, data: dict) -> Optional[IntentReply]:
    app = str(data.get("app", "")).strip()
    if not app:
        return None
    import actions

    return IntentReply(actions.open_app(app))


def _dispatch_run_command(chat_id: str, data: dict) -> Optional[IntentReply]:
    command = str(data.get("command", "")).strip()
    if not command:
        return None
    import actions
    from pathlib import Path

    # Run from the home directory so relative paths ("Desktop", "Downloads")
    # resolve the way a person would expect, not against the bot's repo cwd.
    output = actions.run_command(command, max_chars=1500, cwd=str(Path.home()))
    return IntentReply(f"$ {command}\n{output}")


def _dispatch_read_clipboard(chat_id: str, data: dict) -> Optional[IntentReply]:
    import actions

    return IntentReply(actions.read_clipboard())


def _dispatch_write_clipboard(chat_id: str, data: dict) -> Optional[IntentReply]:
    text = str(data.get("text", ""))
    if not text:
        return None
    import actions

    return IntentReply(actions.write_clipboard(text))


def _dispatch_play_media(chat_id: str, data: dict) -> Optional[IntentReply]:
    query = str(data.get("query", "")).strip()
    if not query:
        return None
    import actions

    return IntentReply(actions.play_media(query))


def _dispatch_get_weather(chat_id: str, data: dict) -> Optional[IntentReply]:
    location = str(data.get("location", "")).strip()
    if not location:
        return None
    import actions

    return IntentReply(actions.get_weather(location))


def _text_handler(func: Callable[[str], str]) -> Callable[[str, dict], Optional[IntentReply]]:
    """Wrap a plain text-returning module call into an IntentReply handler."""
    return lambda chat_id, data: IntentReply(func(chat_id))


_DISPATCH: dict[str, Callable[[str, dict], Optional[IntentReply]]] = {
    "add_expense": _dispatch_add_expense,
    "add_task": _dispatch_add_task,
    "complete_task": _dispatch_complete_task,
    "set_reminder": _dispatch_set_reminder,
    "list_tasks": _text_handler(tasks.list_tasks),
    "budget_status": _text_handler(expenses.budget_status),
    "list_expenses": lambda chat_id, data: IntentReply(expenses.list_expenses(chat_id, [])),
    "daily_summary": _text_handler(digest.build_daily_digest),
    "gold_price": lambda chat_id, data: IntentReply(finance.gold_price(chat_id, [])),
    "ter_price": lambda chat_id, data: IntentReply(finance.ter_price(chat_id, [])),
    "take_screenshot": _dispatch_screenshot,
    "open_app": _dispatch_open_app,
    "run_command": _dispatch_run_command,
    "read_clipboard": _dispatch_read_clipboard,
    "write_clipboard": _dispatch_write_clipboard,
    "play_media": _dispatch_play_media,
    "get_weather": _dispatch_get_weather,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def route(chat_id: str, text: str) -> Optional[IntentReply]:
    """Try to handle plain text as an actionable intent.

    Args:
        chat_id: The canonical owner ID (config.OWNER_ID).
        text: The raw message text (already checked to not be a slash command).

    Returns:
        An IntentReply if an intent was recognised and executed, or None to
        signal the caller should fall through to the LLM chat path.
    """
    text = text.strip()
    if not text or text.startswith("/"):
        return None

    reply = _fast_path(chat_id, text)
    if reply is not None:
        logger.info("Intent fast-path handled: %.60s", text)
        return reply

    data = _classify(text)
    if not data or not isinstance(data, dict):
        return None

    intent = data.get("intent")
    handler = _DISPATCH.get(intent)
    if handler is None:  # "chat" or anything unrecognised → normal chat path
        return None

    try:
        reply = handler(chat_id, data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Intent handler '%s' failed: %s", intent, exc)
        return None

    if reply is not None:
        logger.info("Intent '%s' handled: %.60s", intent, text)
    return reply
