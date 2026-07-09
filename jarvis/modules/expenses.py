"""
modules/expenses.py — Expense tracking from payment-screenshot uploads.

Pipeline: OCR (pytesseract) extracts raw text from the screenshot -> the
local LLM structures that text into amount/recipient/date/remarks (one-shot
call, same pattern as communication.draft_email / decision.decide) -> stored
as an expense row. The LLM never sees the image itself — llama3.2 is
text-only; only the OCR text is passed to it.
"""

import datetime
import json
import logging
from pathlib import Path
from typing import Optional

import pytesseract
from PIL import Image, ImageOps

import memory
from config import DATA_DIR
from store.db import get_connection

logger = logging.getLogger(__name__)

RECEIPTS_DIR = DATA_DIR / "receipts"

_EXTRACT_SYSTEM = (
    "You extract structured data from OCR text of a mobile banking payment "
    "confirmation screenshot. The OCR may contain noise/garbled characters.\n\n"
    "Return ONLY a JSON object with these exact keys:\n"
    "  amount: number (the transaction amount, digits only, no currency symbol or commas)\n"
    "  recipient: string — the money's DESTINATION only. Look specifically for a field "
    "labelled 'To', 'Beneficiary name', or similar. NEVER use a 'From'/'From Account' name "
    "here even if it appears first in the text. If only a bank name is present with no "
    "specific person/account name, use the bank name. Use null if truly nothing indicates a destination.\n"
    "  date: string (the date as written in the text, or null if not found)\n"
    "  remarks: string — ONLY the text that appears directly next to a field labelled "
    "'Remarks' or 'Purpose'. If that field is blank/empty in the source text, use null. "
    "Never substitute a bank name, address, or account number for remarks.\n\n"
    "If a field cannot be determined confidently, use null. Do not guess or invent "
    "values. Output ONLY the JSON object, no other text."
)


def _preprocess(image_path: str) -> Image.Image:
    """Grayscale + autocontrast + 2x upscale — meaningfully improves OCR
    accuracy on low-contrast screenshots (colored backgrounds, small text)."""
    img = Image.open(image_path).convert("L")
    img = ImageOps.autocontrast(img)
    return img.resize((img.width * 2, img.height * 2))


def _ocr(image_path: str) -> str:
    """Run OCR on a screenshot, after contrast-improving preprocessing."""
    return pytesseract.image_to_string(_preprocess(image_path))


def _extract_fields(ocr_text: str) -> dict:
    """Structure raw OCR text into amount/recipient/date/remarks via the local LLM.

    Args:
        ocr_text: Raw text pulled from the screenshot by _ocr().

    Returns:
        A dict with keys amount/recipient/date/remarks (any may be None).
        On LLM or JSON-parse failure, returns all-None fields rather than
        raising — the caller can still store the raw OCR text for manual review.
    """
    import brain  # local import — avoids loading the LLM at module import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": ocr_text},
            ],
            max_tokens=200,
            temperature=0.1,
        )
        raw = response["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw)
        return {
            "amount": parsed.get("amount"),
            "recipient": parsed.get("recipient"),
            "date": parsed.get("date"),
            "remarks": parsed.get("remarks"),
        }
    except (json.JSONDecodeError, KeyError, Exception) as exc:  # noqa: BLE001
        logger.exception("Field extraction failed: %s", exc)
        return {"amount": None, "recipient": None, "date": None, "remarks": None}


def add_expense_from_image(chat_id: str, image_path: str, caption: Optional[str] = None) -> str:
    """OCR + extract + store an expense from a payment-screenshot image.

    Args:
        chat_id: The owner this expense belongs to.
        image_path: Local filesystem path to the downloaded screenshot.
        caption: Optional text the user sent alongside the image — used as
                 the remarks if provided, overriding whatever OCR/LLM found
                 (the user's own words are more trustworthy than an OCR guess).

    Returns:
        A confirmation summarizing what was logged, or an error message if
        OCR/extraction failed outright.
    """
    try:
        ocr_text = _ocr(image_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR failed for %s: %s", image_path, exc)
        return "Couldn't read that image — try a clearer screenshot."

    fields = _extract_fields(ocr_text)
    remarks = caption.strip() if caption and caption.strip() else fields["remarks"]

    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{Path(image_path).suffix}"
    stored_path = RECEIPTS_DIR / stored_name
    try:
        Image.open(image_path).save(stored_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to archive receipt image: %s", exc)
        stored_name = None

    amount = fields["amount"]
    try:
        amount = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount = None

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, recipient, remarks, raw_ocr_text, "
            "image_filename, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                amount,
                fields["recipient"],
                remarks,
                ocr_text,
                stored_name,
                datetime.datetime.now().isoformat(),
            ),
        )

    if amount is None:
        return (
            "Logged the receipt, but couldn't confidently read the amount — "
            "check /expenses to review and correct it if needed."
        )

    line = f"Logged expense: {amount:,.2f} BTN"
    if fields["recipient"]:
        line += f" to {fields['recipient']}"
    if remarks:
        line += f" — {remarks}"
    return line


def list_expenses(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """List the most recent expenses for this chat.

    Args:
        chat_id: The owner to list expenses for.
        args: Optional [N] to control how many entries to show (default 10).

    Returns:
        A newline-separated list, or a message if there are none.
    """
    args = args or []
    limit = 10
    if args and args[0].isdigit():
        limit = int(args[0])

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, amount, currency, recipient, remarks, created_at FROM expenses "
            "WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()

    if not rows:
        return "No expenses logged yet. Send a payment screenshot to log one."

    lines = []
    for row in rows:
        when = datetime.datetime.fromisoformat(row["created_at"]).strftime("%b %-d")
        amount_str = f"{row['amount']:,.2f}" if row["amount"] is not None else "?"
        line = f"#{row['id']} {when} — {amount_str} {row['currency']}"
        if row["recipient"]:
            line += f" to {row['recipient']}"
        if row["remarks"]:
            line += f" ({row['remarks']})"
        lines.append(line)
    return "\n".join(lines)


def set_budget(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Set or clear the monthly budget threshold used by budget_status().

    Args:
        chat_id: Unused — the budget is a single global preference, matching
                 how gold/TER targets are stored (see memory.py).
        args: ["<amount>"] to set, or ["clear"] to remove.

    Returns:
        A confirmation or usage string.
    """
    args = args or []
    if not args:
        return "Usage: /budget set <amount> | /budget set clear"

    if args[0].lower() == "clear":
        memory.set_preference("monthly_budget", None)
        return "Monthly budget cleared."

    try:
        amount = float(args[0])
    except ValueError:
        return "Usage: /budget set <amount> | /budget set clear"

    if amount <= 0:
        return "Budget must be positive."

    memory.set_preference("monthly_budget", amount)
    return f"Monthly budget set to {amount:,.2f} BTN."


def budget_status(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Report this calendar month's spend against the configured budget.

    Args:
        chat_id: The owner to total expenses for.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        Spend total, and remaining/over-budget amount if a budget is set.
    """
    month_start = datetime.date.today().replace(day=1).isoformat()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n FROM expenses "
            "WHERE chat_id = ? AND date(created_at) >= ? AND amount IS NOT NULL",
            (chat_id, month_start),
        ).fetchone()

    total, count = row["total"], row["n"]
    month_name = datetime.date.today().strftime("%B")
    lines = [f"{month_name}: {total:,.2f} BTN spent across {count} expense{'s' if count != 1 else ''}."]

    budget = memory.get_preference("monthly_budget")
    if budget:
        remaining = float(budget) - total
        if remaining >= 0:
            lines.append(f"Budget: {float(budget):,.2f} BTN — {remaining:,.2f} remaining.")
        else:
            lines.append(f"Budget: {float(budget):,.2f} BTN — over by {-remaining:,.2f}.")

    return "\n".join(lines)
