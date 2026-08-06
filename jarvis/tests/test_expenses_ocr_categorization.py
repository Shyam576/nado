"""tests/test_expenses_ocr_categorization.py — category matching robustness.

Covers the fallback-matching hardening in _classify_category (stray
punctuation, substring recovery) with a fake LLM response — the OCR/PSM and
amount-decimal fixes themselves were verified manually against real receipt
images archived in data/receipts/ (see conversation/commit history), since
they depend on the real local LLM and aren't practical to assert in a fast
unit test.
"""

from modules import expenses


def _fake_llm(answer_text):
    class _FakeLLM:
        def create_chat_completion(self, **kwargs):
            return {"choices": [{"message": {"content": answer_text}}]}

    return _FakeLLM()


def test_classify_category_strips_trailing_punctuation(monkeypatch):
    import brain

    monkeypatch.setattr(brain, "_get_llm", lambda: _fake_llm("Food."))
    assert expenses._classify_category(None, "lunch") == "Food"


def test_classify_category_strips_wrapping_quotes(monkeypatch):
    import brain

    monkeypatch.setattr(brain, "_get_llm", lambda: _fake_llm('"Drinking"'))
    assert expenses._classify_category(None, "beers") == "Drinking"


def test_classify_category_recovers_single_substring_match(monkeypatch):
    import brain

    monkeypatch.setattr(brain, "_get_llm", lambda: _fake_llm("Category: Transport"))
    assert expenses._classify_category(None, "taxi") == "Transport"


def test_classify_category_falls_back_on_ambiguous_multi_match(monkeypatch):
    import brain

    monkeypatch.setattr(brain, "_get_llm", lambda: _fake_llm("Food or Junk, not sure"))
    assert expenses._classify_category(None, "snack") == "Miscellaneous"


def test_classify_category_falls_back_on_unrecognised_answer(monkeypatch):
    import brain

    monkeypatch.setattr(brain, "_get_llm", lambda: _fake_llm("I don't know"))
    assert expenses._classify_category(None, "something") == "Miscellaneous"
