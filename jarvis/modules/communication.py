"""
modules/communication.py — Email drafting and message rewriting via one-shot LLM calls.

Uses a separate system prompt and a fresh one-shot call to brain's Llama
singleton (not brain.ask()), so neither pollutes the main conversation
history — same pattern as proactive.py's _ollama_call.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DRAFT_EMAIL_SYSTEM = (
    "You are drafting a professional email on the user's behalf. Given a short "
    "description of what they want to say, write a complete email with a Subject "
    "line and a body. Keep the tone professional but not stiff. Do not invent "
    "specific facts, names, or numbers the user didn't provide — leave clear "
    "placeholders like [recipient name] where needed. Output only the email, "
    "formatted as:\nSubject: ...\n\n<body>"
)

_REWRITE_TONES = {"professional", "casual", "formal", "friendly", "concise", "assertive"}

_REWRITE_SYSTEM_TEMPLATE = (
    "Rewrite the user's message in a {tone} tone. Preserve the original meaning and "
    "every factual detail — do not add or remove information, only change the phrasing "
    "and tone. Output ONLY the rewritten text, nothing else (no preamble, no quotes)."
)

_MEETING_NOTES_SYSTEM = (
    "You summarise raw meeting notes into a JSON object with two keys:\n"
    '  summary: a 2-4 sentence plain-text summary of what was discussed/decided\n'
    "  action_items: a list of short, concrete task strings (e.g. \"Follow up with "
    "finance on Q3 budget\"), extracted only from things explicitly stated as a "
    "to-do/follow-up/action in the notes. Do not invent action items that aren't "
    "actually there — an empty list is fine if none are stated.\n\n"
    "Output ONLY the JSON object, no other text."
)


def draft_email(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Draft an email from a short description via a one-shot LLM call.

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: Words describing what the email should say, e.g.
              ["tell", "the", "team", "the", "deploy", "is", "delayed"].

    Returns:
        A drafted email (Subject + body), or a usage message if no
        description was given.
    """
    args = args or []
    description = " ".join(args).strip()
    if not description:
        return "Usage: /draft-email <what you want to say>"

    import brain  # local import — avoids loading the LLM at module import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _DRAFT_EMAIL_SYSTEM},
                {"role": "user", "content": description},
            ],
            max_tokens=500,
            temperature=0.7,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("draft_email LLM call failed: %s", exc)
        return "Something went wrong drafting that email — try again shortly."


def rewrite_message(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Rewrite a message in a different tone via a one-shot LLM call.

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: [tone, text_words...] if the first word matches a known tone
              (see _REWRITE_TONES), else the tone defaults to "professional"
              and all args are treated as the text to rewrite.

    Returns:
        The rewritten text, or a usage message if no text was given.
    """
    args = args or []
    if not args:
        return f"Usage: /rewrite [{'|'.join(sorted(_REWRITE_TONES))}] <text>"

    if args[0].lower() in _REWRITE_TONES:
        tone = args[0].lower()
        text = " ".join(args[1:]).strip()
    else:
        tone = "professional"
        text = " ".join(args).strip()

    if not text:
        return f"Usage: /rewrite [{'|'.join(sorted(_REWRITE_TONES))}] <text>"

    import brain  # local import — avoids loading the LLM at module import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM_TEMPLATE.format(tone=tone)},
                {"role": "user", "content": text},
            ],
            max_tokens=400,
            temperature=0.6,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("rewrite_message LLM call failed: %s", exc)
        return "Something went wrong rewriting that — try again shortly."


def process_meeting_notes(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Summarise raw meeting notes and auto-create any stated action items as tasks.

    Args:
        chat_id: The owner whose task list new action items get added to.
        args: The raw notes text, as words.

    Returns:
        A summary plus a list of any tasks created, or a usage message.
    """
    args = args or []
    notes = " ".join(args).strip()
    if not notes:
        return "Usage: /notes <raw meeting notes text>"

    import brain  # local import — avoids loading the LLM at module import time
    from modules import tasks  # local import avoids a module-load cycle at import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _MEETING_NOTES_SYSTEM},
                {"role": "user", "content": notes},
            ],
            max_tokens=500,
            temperature=0.3,
        )
        parsed = json.loads(response["choices"][0]["message"]["content"].strip())
    except (json.JSONDecodeError, KeyError, Exception) as exc:  # noqa: BLE001
        logger.exception("process_meeting_notes LLM call failed: %s", exc)
        return "Something went wrong summarising those notes — try again shortly."

    summary = parsed.get("summary") or "(no summary)"
    action_items = parsed.get("action_items") or []

    lines = [f"Summary: {summary}"]
    if action_items:
        lines.append("")
        lines.append(f"Created {len(action_items)} task(s):")
        for item in action_items:
            tasks.add_task(chat_id, item.split())
            lines.append(f"  • {item}")
    return "\n".join(lines)
