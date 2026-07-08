"""
modules/decision.py — Decision support via a one-shot LLM call with live context.

Answers "should I..." / "what's most urgent" style questions by injecting
relevant live data (pending tasks, gold/TER prices when mentioned) into the
prompt, then asking the LLM for a clear recommendation with brief reasoning.
Uses a fresh one-shot call, not brain.ask(), so it doesn't pollute the main
conversation history — same pattern as communication.draft_email.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DECISION_SYSTEM = (
    "You are Jarvis's decision-support brain. The user will ask a question about "
    "what to prioritise, whether to act on something, or how risky a choice is. "
    "Use the context provided (if any) to ground your answer in their actual "
    "situation. Give a clear, direct recommendation first, then 1-2 sentences of "
    "reasoning. Do not hedge excessively. If the context is insufficient to answer "
    "confidently, say so plainly rather than guessing."
)


def _gather_context(chat_id: str, question: str) -> str:
    """Build a compact context block relevant to the question, keyword-driven.

    Args:
        chat_id: The chat whose task data to include.
        question: The user's raw question — scanned for keywords (gold, ter)
                  to decide which live price data is worth the extra fetch.

    Returns:
        A plain-text context block (may be empty if nothing relevant applies).
    """
    from modules import tasks  # local import avoids a module-load cycle at import time

    parts: list[str] = []

    pending = tasks.list_tasks(chat_id)
    if "No pending tasks" not in pending:
        parts.append(pending)

    lowered = question.lower()
    if "gold" in lowered:
        from modules import finance

        parts.append(finance.gold_price(chat_id))
    if "ter" in lowered:
        from modules import finance

        parts.append(finance.ter_price(chat_id, ["all"]))

    return "\n".join(parts)


def decide(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Answer a decision/priority question, grounded in live context.

    Args:
        chat_id: The chat asking the question (used to pull their task data).
        args: The question words, e.g. ["should", "i", "buy", "gold", "today?"].

    Returns:
        A recommendation with brief reasoning, or a usage message.
    """
    args = args or []
    question = " ".join(args).strip()
    if not question:
        return "Usage: /decide <your question>"

    import brain  # local import — avoids loading the LLM at module import time

    context = _gather_context(chat_id, question)
    user_content = f"Context:\n{context}\n\nQuestion: {question}" if context else question

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _DECISION_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=300,
            temperature=0.8,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("decide LLM call failed: %s", exc)
        return "Something went wrong working that out — try again shortly."
