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

# Fixed category set — a small model classifies far more reliably into a
# short fixed list than it extracts/invents free-form labels.
CATEGORIES: list[str] = [
    "Food",
    "Transport",
    "Utilities/Bills",
    "Family Support",
    "Health",
    "Investment/Gold",
    "Drinking",
    "Junk",
    "Snooker",
    "Miscellaneous",
]

_EXTRACT_SYSTEM = (
    "You extract structured data from OCR text of a mobile banking payment "
    "confirmation screenshot. The OCR may contain noise/garbled characters.\n\n"
    "Return ONLY a JSON object with these exact keys:\n"
    "  amount: number (the transaction amount as a plain decimal, e.g. 'Nu. 150.00' -> 150.00. "
    "Strip the currency symbol and thousands-separating commas, but ALWAYS keep the decimal "
    "point — never merge the fractional part into the whole number, e.g. 150.00 is 150.00, "
    "never 15000)\n"
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

_CATEGORY_SYSTEM = (
    "Classify this expense into exactly one of the following categories, based on the "
    "recipient name and remarks given. Categories:\n" + "\n".join(f"- {c}" for c in CATEGORIES) + "\n\n"
    "Guidance: 'Food' covers meals, restaurants, groceries, lunch/dinner/breakfast. 'Transport' "
    "covers taxis, buses, fuel, ride-hailing. 'Utilities/Bills' covers phone/mobile data recharges, "
    "electricity, water, internet, rent. 'Health' covers pharmacy, doctor, hospital, medicine. "
    "'Investment/Gold' covers gold/precious-metal purchases. 'Family Support' covers money sent to "
    "family members. 'Drinking' covers alcohol/bars. 'Junk' covers snacks/fast food (distinct from "
    "'Food' which is regular meals/groceries). 'Snooker' covers snooker/pool halls. 'Miscellaneous' "
    "is for anything that doesn't clearly fit another category — use it rather than forcing a poor "
    "fit.\n\n"
    "Respond with ONLY the exact category name from the list above, nothing else."
)


def _preprocess(image_path: str) -> Image.Image:
    """Grayscale + autocontrast + 2x upscale — meaningfully improves OCR
    accuracy on low-contrast screenshots (colored backgrounds, small text)."""
    img = Image.open(image_path).convert("L")
    img = ImageOps.autocontrast(img)
    return img.resize((img.width * 2, img.height * 2))


def _ocr(image_path: str) -> str:
    """Run OCR on a screenshot, after contrast-improving preprocessing.

    Forces PSM 6 ("assume a single uniform block of text") rather than
    tesseract's default auto page-segmentation (PSM 3). Verified against
    real banking-app screenshots (two-column "label : value" layouts):
    PSM 3 inconsistently reorders text into two separate blocks — all
    labels, then all values, in a different order — which silently
    disconnects a field like "Purpose/Bill QR:" from its actual value
    ("Lunch"), causing _extract_fields() to return remarks=None even
    though the text was captured. PSM 6 reliably keeps each label next to
    its value on one line instead.
    """
    return pytesseract.image_to_string(_preprocess(image_path), config="--psm 6")


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
            # Headroom above what a 4-field JSON object needs — the model
            # sometimes pretty-prints with indentation/whitespace instead of
            # compact JSON, which costs more tokens for the same data.
            # Verified: at 200 this occasionally truncated mid-object,
            # producing invalid JSON with no closing brace.
            max_tokens=300,
            # 0.0, not 0.1 — this is structured field extraction with exactly
            # one correct answer per field, not a task with room for
            # variation. Verified 0.1 occasionally mis-transcribes a numeric
            # amount (e.g. "150.00" -> "15000") across repeated runs on the
            # same OCR text; 0.0 removes that source of non-determinism.
            temperature=0.0,
            # Grammar-constrained JSON output (same as modules/intent.py's
            # classifier) — guarantees syntactically valid, closed JSON
            # regardless of how the model chooses to format it, rather than
            # hoping free-text generation happens to stay well-formed.
            response_format={"type": "json_object"},
        )
        raw = response["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw)
        return {
            "amount": parsed.get("amount"),
            "recipient": parsed.get("recipient"),
            "date": parsed.get("date"),
            "remarks": parsed.get("remarks"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Field extraction failed: %s", exc)
        return {"amount": None, "recipient": None, "date": None, "remarks": None}


def _classify_category(recipient: Optional[str], remarks: Optional[str]) -> str:
    """Classify an expense into one of CATEGORIES using the recipient/remarks text.

    Args:
        recipient: The extracted recipient name, if any.
        remarks: The extracted (or user-supplied) remarks, if any.

    Returns:
        One of CATEGORIES. Falls back to "Miscellaneous" on any failure or
        if the model's response doesn't exactly match a known category.
    """
    import brain  # local import — avoids loading the LLM at module import time

    context = f"Recipient: {recipient or 'unknown'}\nRemarks: {remarks or 'none'}"
    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _CATEGORY_SYSTEM},
                {"role": "user", "content": context},
            ],
            max_tokens=20,
            temperature=0.1,
        )
        answer = response["choices"][0]["message"]["content"].strip()
        # Strip stray wrapping punctuation/quotes the model sometimes adds
        # despite being told to answer with only the category name — an
        # exact-match-only check would otherwise silently default to
        # Miscellaneous over something as trivial as a trailing period.
        cleaned = answer.strip(" .!\"'`")
        for category in CATEGORIES:
            if category.lower() == cleaned.lower():
                return category
        # Last resort: the model named a category somewhere in a longer
        # answer (e.g. it ignored the "nothing else" instruction) — accept
        # it only if exactly one category name appears as a substring, to
        # avoid guessing between two.
        contained = [c for c in CATEGORIES if c.lower() in cleaned.lower()]
        if len(contained) == 1:
            return contained[0]
        logger.warning("Category classification returned unrecognised value: %r", answer)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Category classification failed: %s", exc)
    return "Miscellaneous"


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
    category = _classify_category(fields["recipient"], remarks)

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
            "INSERT INTO expenses (chat_id, amount, recipient, remarks, category, raw_ocr_text, "
            "image_filename, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                amount,
                fields["recipient"],
                remarks,
                category,
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

    line = f"Logged expense: {amount:,.2f} BTN [{category}]"
    if fields["recipient"]:
        line += f" to {fields['recipient']}"
    if remarks:
        line += f" — {remarks}"
    return line


def add_expense_from_text(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Log an expense from a typed amount + description, no screenshot needed.

    Args:
        chat_id: The owner this expense belongs to.
        args: [amount, description_words...] e.g. ["500", "lunch", "with", "team"].
              Only the leading amount is parsed structurally (fast, reliable,
              no LLM needed for that part) — same "structured input, no LLM"
              principle as every other command. Category classification still
              uses the LLM since that's a judgment call, same as the image flow.

    Returns:
        A confirmation, or a usage message if no valid leading amount was given.
    """
    args = args or []
    if not args:
        return "Usage: /spend <amount> <description>"

    try:
        amount = float(args[0])
    except ValueError:
        return "Usage: /spend <amount> <description>"

    if amount <= 0:
        return "Amount must be positive."

    remarks = " ".join(args[1:]).strip() or None
    category = _classify_category(None, remarks)

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO expenses (chat_id, amount, remarks, category, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, amount, remarks, category, datetime.datetime.now().isoformat()),
        )

    line = f"Logged expense: {amount:,.2f} BTN [{category}]"
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
            "SELECT id, amount, currency, recipient, remarks, category, created_at FROM expenses "
            "WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()

    if not rows:
        return "No expenses logged yet. Send a payment screenshot to log one."

    lines = []
    for row in rows:
        when = datetime.datetime.fromisoformat(row["created_at"]).strftime("%b %-d")
        amount_str = f"{row['amount']:,.2f}" if row["amount"] is not None else "?"
        category = row["category"] or "Uncategorised"
        line = f"#{row['id']} {when} — {amount_str} {row['currency']} [{category}]"
        if row["recipient"]:
            line += f" to {row['recipient']}"
        if row["remarks"]:
            line += f" ({row['remarks']})"
        lines.append(line)
    return "\n".join(lines)


def _most_recent_expense_id(chat_id: str) -> Optional[int]:
    """Return the ID of this chat's most recently logged expense, or None if none exist."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM expenses WHERE chat_id = ? ORDER BY created_at DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
    return row["id"] if row else None


def correct_expense(
    chat_id: str,
    expense_id: Optional[int],
    amount: Optional[float],
    description: Optional[str],
) -> str:
    """Correct an expense's amount and/or what it was for, in plain language.

    Unlike set_category() (which requires an exact category name from
    CATEGORIES), `description` is freeform — e.g. "vegetables", "lunch with
    team" — and gets re-classified through the same LLM category classifier
    used when an expense is first logged, so the user never needs to know
    or type an exact category name.

    Args:
        chat_id: The owner attempting the correction (ownership check).
        expense_id: The expense to correct, or None to target the most
                    recently logged one.
        amount: New amount, or None to leave the amount unchanged.
        description: What the expense was actually for, or None to leave it
                     unchanged. Also becomes the expense's new remarks.

    Returns:
        A confirmation (one line per field corrected), or an error/usage message.
    """
    if expense_id is None:
        expense_id = _most_recent_expense_id(chat_id)
        if expense_id is None:
            return "You don't have any expenses logged yet."

    if amount is None and not description:
        return "Tell me what to change — an amount, what it was for, or both."

    results = []
    if amount is not None:
        results.append(set_amount(chat_id, [str(expense_id), str(amount)]))

    if description:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT chat_id, recipient FROM expenses WHERE id = ?", (expense_id,)
            ).fetchone()
        if row is None or row["chat_id"] != chat_id:
            results.append(f"No expense #{expense_id} found.")
        else:
            category = _classify_category(row["recipient"], description)
            with get_connection() as conn:
                conn.execute(
                    "UPDATE expenses SET remarks = ?, category = ? WHERE id = ?",
                    (description, category, expense_id),
                )
            results.append(f"Expense #{expense_id} updated: {description} [{category}].")

    return "\n".join(results)


def set_amount(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Manually correct the amount of a previously logged expense.

    Args:
        chat_id: The owner attempting the correction (ownership check).
        args: [id, amount].

    Returns:
        A confirmation, or an error/usage message.
    """
    args = args or []
    if len(args) < 2 or not args[0].isdigit():
        return "Usage: /expenses amount <id> <value>"

    expense_id = int(args[0])
    try:
        amount = float(args[1])
    except ValueError:
        return "Usage: /expenses amount <id> <value>"

    if amount <= 0:
        return "Amount must be positive."

    with get_connection() as conn:
        row = conn.execute("SELECT chat_id FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if row is None or row["chat_id"] != chat_id:
            return f"No expense #{expense_id} found."
        conn.execute("UPDATE expenses SET amount = ? WHERE id = ?", (amount, expense_id))

    return f"Expense #{expense_id} amount corrected to {amount:,.2f} BTN."


def list_categories(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Return the fixed list of expense categories.

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A newline-separated list of every category expenses can be classified into.
    """
    return "Categories:\n" + "\n".join(f"  {c}" for c in CATEGORIES)


def set_category(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Manually correct the category of a previously logged expense.

    Args:
        chat_id: The owner attempting the correction (ownership check).
        args: [id, category_words...] — category may be multi-word
              (e.g. "Investment Gold" is matched case-insensitively
              against CATEGORIES regardless of spacing/slashes).

    Returns:
        A confirmation, or an error/usage message.
    """
    args = args or []
    if len(args) < 2 or not args[0].isdigit():
        return f"Usage: /expenses category <id> <category>\nCategories: {', '.join(CATEGORIES)}"

    expense_id = int(args[0])
    requested = " ".join(args[1:]).strip().lower().replace("/", " ")
    match = next((c for c in CATEGORIES if c.lower().replace("/", " ") == requested), None)
    if match is None:
        return f"Unknown category. Choose one of: {', '.join(CATEGORIES)}"

    with get_connection() as conn:
        row = conn.execute("SELECT chat_id FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if row is None or row["chat_id"] != chat_id:
            return f"No expense #{expense_id} found."
        conn.execute("UPDATE expenses SET category = ? WHERE id = ?", (match, expense_id))

    return f"Expense #{expense_id} recategorised to {match}."


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


def weekly_expense_summary(chat_id: str) -> dict:
    """Return the last 7 days' spend total and per-category breakdown.

    Args:
        chat_id: The owner to total expenses for.

    Returns:
        A dict with keys: total (float), by_category (dict[str, float]).
    """
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT COALESCE(category, 'Uncategorised') AS category, SUM(amount) AS total "
            "FROM expenses WHERE chat_id = ? AND created_at >= ? AND amount IS NOT NULL "
            "GROUP BY category ORDER BY total DESC",
            (chat_id, week_ago),
        ).fetchall()

    by_category = {row["category"]: row["total"] for row in rows}
    return {"total": sum(by_category.values()), "by_category": by_category}


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

    if count:
        with get_connection() as conn:
            category_rows = conn.execute(
                "SELECT COALESCE(category, 'Uncategorised') AS category, SUM(amount) AS total "
                "FROM expenses WHERE chat_id = ? AND date(created_at) >= ? AND amount IS NOT NULL "
                "GROUP BY category ORDER BY total DESC",
                (chat_id, month_start),
            ).fetchall()
        lines.append("")
        lines.append("By category:")
        lines.extend(f"  {row['category']}: {row['total']:,.2f}" for row in category_rows)

    return "\n".join(lines)
