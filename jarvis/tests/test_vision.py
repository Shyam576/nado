"""tests/test_vision.py — screenshot -> OCR -> explain, without a real screen or LLM."""

import actions
from modules import vision


def test_explain_screenshot_returns_explanation_and_path(monkeypatch, tmp_path):
    fake_screenshot = tmp_path / "shot.png"
    fake_screenshot.write_bytes(b"not a real image, just a placeholder")

    monkeypatch.setattr(actions, "take_screenshot", lambda: str(fake_screenshot))
    monkeypatch.setattr(vision, "_ocr", lambda path: "TypeError: cannot read property 'x' of undefined")

    class _FakeLLM:
        def create_chat_completion(self, **kwargs):
            return {"choices": [{"message": {"content": "This is a JavaScript TypeError — check for a null value."}}]}

    import brain

    monkeypatch.setattr(brain, "_get_llm", lambda: _FakeLLM())

    text, image_path = vision.explain_screenshot()
    assert "TypeError" in text
    assert image_path == str(fake_screenshot)


def test_explain_screenshot_handles_capture_failure(monkeypatch):
    def _boom():
        raise RuntimeError("screencapture failed")

    monkeypatch.setattr(actions, "take_screenshot", _boom)

    text, image_path = vision.explain_screenshot()
    assert "Couldn't capture" in text
    assert image_path is None


def test_explain_screenshot_handles_empty_ocr(monkeypatch, tmp_path):
    fake_screenshot = tmp_path / "shot.png"
    fake_screenshot.write_bytes(b"placeholder")

    monkeypatch.setattr(actions, "take_screenshot", lambda: str(fake_screenshot))
    monkeypatch.setattr(vision, "_ocr", lambda path: "   ")

    text, image_path = vision.explain_screenshot()
    assert "doesn't seem to contain any readable text" in text
    assert image_path == str(fake_screenshot)
