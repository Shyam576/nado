"""
command_confirmation.py — shared "confirm before running a shell command" gate.

Both the voice pipeline (actions.parse_and_execute) and every bot transport
(via modules/intent.py, shared by Telegram/Discord) funnel run_command
requests through here: a command is never executed on the first ask — it's
parked for CONFIRM_WINDOW_SECONDS and only runs if the very next message
from the same identity is an explicit "yes". Sharing one module (rather than
each entry point keeping its own pending-command state) means the rule is
identical everywhere run_command is reachable, and was previously not true:
voice mode used to execute run_command immediately with no gate at all.
"""

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

CONFIRM_WINDOW_SECONDS = 60
_pending: dict[str, tuple[str, float]] = {}  # identity -> (command, queued_at)

_CONFIRM_RE = re.compile(r"^(?:yes|y|confirm|do it|run it)[.!]?$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^(?:no|n|cancel|stop|abort)[.!]?$", re.IGNORECASE)


def stage(identity: str, command: str) -> str:
    """Park a command for confirmation and return the prompt to show the user.

    Args:
        identity: Who asked — config.OWNER_ID for every current caller
                  (voice mode and every bot transport are all "the owner").
        command: The shell command string to hold for confirmation.

    Returns:
        The confirmation prompt to show/speak to the user.
    """
    _pending[identity] = (command, time.time())
    return f"Run this?\n$ {command}\nReply “yes” within {CONFIRM_WINDOW_SECONDS}s to execute, “no” to cancel."


def check(identity: str, text: str, allow_execution: bool = True) -> Optional[str]:
    """Resolve a parked command if `text` confirms or cancels it.

    Args:
        identity: Same identity key passed to stage().
        text: The next message/utterance from that identity.
        allow_execution: False when `text` is being processed via replay
            (e.g. Discord catch-up after a reconnect) rather than live —
            in that case a matching "yes" must NOT execute the command.
            Without this, a run_command request and its "yes" reply, both
            sent while the bot was offline, would replay back-to-back and
            silently re-execute the moment the bot reconnects, regardless
            of how long ago they were actually sent. A replayed "yes"
            requires a fresh, live confirmation instead.

    Returns:
        The result string if `text` was consumed as a confirm/cancel reply
        (or as a stale/replayed reply), or None if there is nothing
        pending / `text` isn't a yes/no reply — callers should treat None
        as "not a confirmation, handle this as a normal message".
    """
    pending = _pending.get(identity)
    if pending is None:
        return None

    command, queued_at = pending
    if time.time() - queued_at > CONFIRM_WINDOW_SECONDS:
        del _pending[identity]
        if _CONFIRM_RE.match(text):
            return "That command request expired — ask again if you still want it."
        return None  # window passed; treat the message as a fresh request

    if _CONFIRM_RE.match(text):
        del _pending[identity]
        if not allow_execution:
            return (
                f"That confirmation for '{command}' arrived while I was catching up on "
                "missed messages — ask again if you still want it to run."
            )
        import actions
        from pathlib import Path

        output = actions.run_command(command, max_chars=1500, cwd=str(Path.home()))
        return f"$ {command}\n{output}"

    if _CANCEL_RE.match(text):
        del _pending[identity]
        return "Cancelled — nothing was run."

    # Any other message implicitly abandons the pending command.
    del _pending[identity]
    return None
