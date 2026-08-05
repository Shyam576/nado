"""
modules/vision.py — Screenshot OCR + LLM explanation.

Takes a screenshot of the user's own screen, OCRs it, and asks the local LLM
to explain what it shows or suggest a fix — e.g. for a terminal error or a
cryptic dialog box. Same "OCR text only, LLM never sees the image" principle
as modules/expenses.py, since the local model is text-only.
"""

import logging
from typing import Optional

import pytesseract
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

_EXPLAIN_SYSTEM = (
    "You are looking at OCR text extracted from a screenshot of the user's computer screen "
    "(e.g. a terminal error, a stack trace, a dialog box). The OCR may contain noise or "
    "garbled characters. In 2-4 sentences, explain what this shows, and if it looks like an "
    "error, name the likely cause and a concrete next step to fix it. If the text is too "
    "garbled or doesn't look like an error, say plainly what you can make out rather than "
    "inventing details. Plain text only, no markdown."
)


def _preprocess(image_path: str) -> Image.Image:
    """Grayscale + autocontrast + 2x upscale — improves OCR accuracy on screenshots."""
    img = Image.open(image_path).convert("L")
    img = ImageOps.autocontrast(img)
    return img.resize((img.width * 2, img.height * 2))


def _ocr(image_path: str) -> str:
    """Run OCR on a screenshot after contrast-improving preprocessing."""
    return pytesseract.image_to_string(_preprocess(image_path))


def explain_screenshot() -> tuple[str, Optional[str]]:
    """Take a screenshot of the user's screen, OCR it, and have the LLM explain it.

    Returns:
        (reply_text, screenshot_path) — screenshot_path is None only if the
        screenshot capture itself failed, in which case reply_text explains
        why the request could not be completed.
    """
    import actions  # local import — avoids pulling in pyautogui at module import time

    try:
        screenshot_path = actions.take_screenshot()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Screenshot capture failed: %s", exc)
        return "Couldn't capture a screenshot — try again?", None

    try:
        ocr_text = _ocr(screenshot_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR failed for %s: %s", screenshot_path, exc)
        return "Captured the screenshot, but couldn't read any text from it.", screenshot_path

    if not ocr_text.strip():
        return "Captured the screenshot, but it doesn't seem to contain any readable text.", screenshot_path

    import brain  # local import — avoids loading the LLM at module import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _EXPLAIN_SYSTEM},
                {"role": "user", "content": ocr_text},
            ],
            max_tokens=250,
            temperature=0.3,
        )
        explanation = response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Screenshot explanation LLM call failed: %s", exc)
        return "Read the screenshot, but couldn't get an explanation — try again shortly.", screenshot_path

    return explanation, screenshot_path
