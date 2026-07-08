"""
bot/commands.py — Command dispatch table for the Telegram transport.

Structured commands (/today, /tasks, ...) call module functions directly —
no LLM round-trip, per JARVIS_V2_PLAN.md §6. Anything not in COMMANDS falls
through to the LLM chat path in bot/telegram_bot.py.
"""

from typing import Callable

from modules import communication, decision, devops, digest, finance, habits, tasks

NOT_IMPLEMENTED = "Not implemented yet — coming in a later build step."


def _handle_ter(chat_id: str, args: list[str]) -> str:
    """Route /ter subcommands: price lookup (default), or target buy/sell/clear."""
    if args and args[0].lower() == "target":
        return finance.ter_target(chat_id, args[1:])
    return finance.ter_price(chat_id, args)


def _handle_mood(chat_id: str, args: list[str]) -> str:
    """Route /mood subcommands: log (default), or history."""
    if args and args[0].lower() == "history":
        return habits.mood_history(chat_id, args[1:])
    return habits.log_mood(chat_id, args)


def _handle_habit(chat_id: str, args: list[str]) -> str:
    """Route /habit subcommands: log (default), or status."""
    if args and args[0].lower() == "status":
        return habits.habit_status(chat_id, args[1:])
    return habits.log_habit(chat_id, args)


def _handle_tasks(chat_id: str, args: list[str]) -> str:
    """Route /tasks subcommands: list (default), add <title>, done <id>."""
    if not args:
        return tasks.list_tasks(chat_id)

    sub = args[0].lower()
    rest = args[1:]
    if sub == "add":
        return tasks.add_task(chat_id, rest)
    if sub == "done":
        return tasks.complete_task(chat_id, rest)
    return "Usage: /tasks [add <title> | done <id>]"


# Order here is also the order /help displays them in.
HELP_TEXT: dict[str, str] = {
    "/today": "/today — pending task count + reminders due today",
    "/tasks": "/tasks [add <title> | done <id>] — list, add, or complete tasks",
    "/remind": "/remind <minutes> <message> — schedule a one-off reminder",
    "/status": "/status [namespace] — Kubernetes pod health, grouped by deployment",
    "/logs": "/logs <service> [namespace] — recent log lines for a service",
    "/gold": "/gold [target <price> | target clear] — gold spot price / alert target",
    "/ter": "/ter [usd|inr|btn|all] [target buy|sell <price> | target clear] — TER price / alerts",
    "/draft-email": "/draft-email <what to say> — draft an email via LLM",
    "/decide": "/decide <question> — decision support, grounded in your tasks/prices",
    "/mood": "/mood <mood> [1-10] [note] | /mood history — log or view mood entries",
    "/habit": "/habit <name> | /habit status [name] — log a habit or check streaks",
    "/digest": "/digest — tasks, habits, and market movement in one summary (also sent daily at 7 AM)",
    "/help": "/help — show this list",
}

COMMANDS: dict[str, Callable[[str, list[str]], str]] = {
    "/today": lambda chat_id, args: tasks.today_summary(chat_id),
    "/tasks": _handle_tasks,
    "/remind": lambda chat_id, args: tasks.add_reminder(chat_id, args),
    "/status": lambda chat_id, args: devops.k8s_health(chat_id, args),
    "/gold": lambda chat_id, args: finance.gold_price(chat_id, args),
    "/ter": _handle_ter,
    "/logs": lambda chat_id, args: devops.tail_logs(chat_id, args),
    "/draft-email": lambda chat_id, args: communication.draft_email(chat_id, args),
    "/decide": lambda chat_id, args: decision.decide(chat_id, args),
    "/mood": _handle_mood,
    "/habit": _handle_habit,
    "/digest": lambda chat_id, args: digest.build_daily_digest(chat_id),
    "/help": lambda chat_id, args: (
        "Available commands:\n"
        + "\n".join(HELP_TEXT.values())
        + "\n\nAnything else you type (not starting with /) goes to Jarvis as a normal chat message."
    ),
}


def dispatch(chat_id: str, text: str) -> str | None:
    """Route a command string to its handler.

    Args:
        chat_id: The Telegram chat ID the message came from.
        text: The raw message text, e.g. "/today" or "/logs visa-service".

    Returns:
        The handler's reply string, or None if `text` is not a known command
        (caller should fall through to the LLM chat path in that case).
    """
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None

    command = parts[0].lower()
    handler = COMMANDS.get(command)
    if handler is None:
        return None

    args = parts[1:]
    return handler(chat_id, args)
