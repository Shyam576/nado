"""
modules/communication.py — Email drafting via a one-shot LLM call.

Uses a separate system prompt and a fresh one-shot call to brain's Llama
singleton (not brain.ask()), so drafting an email doesn't pollute the main
conversation history — same pattern as proactive.py's _ollama_call.
"""

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
