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


# ---------------------------------------------------------------------------
# Regex amount fallback — used when the LLM extraction path is unavailable
# (e.g. llama-cpp-python not installed, see requirements-server.txt) or fails
# for any other reason. Covers the real production gap: every receipt was
# returning "couldn't confidently read the amount" because _extract_fields()
# fell straight to all-None fields with no fallback at all.
# ---------------------------------------------------------------------------


def test_regex_extract_amount_prefers_labelled_value():
    text = "Some Bank\nTo: Corner Shop\nAmount: Nu. 1,250.50\nRemarks: groceries"
    assert expenses._regex_extract_amount(text) == 1250.50


def test_regex_extract_amount_falls_back_to_any_decimal():
    text = "Payment confirmation\n150.00\nThank you"
    assert expenses._regex_extract_amount(text) == 150.00


def test_regex_extract_amount_returns_none_when_nothing_decimal_shaped():
    assert expenses._regex_extract_amount("garbled OCR noise with no numbers") is None


def test_extract_fields_falls_back_to_regex_amount_when_llm_unavailable(monkeypatch):
    import brain

    def _boom():
        raise ModuleNotFoundError("No module named 'llama_cpp'")

    monkeypatch.setattr(brain, "_get_llm", _boom)

    fields = expenses._extract_fields("Amount: BTN 320.00\nTo: Vendor")
    assert fields["amount"] == 320.00
    assert fields["recipient"] is None
    assert fields["remarks"] is None


# ---------------------------------------------------------------------------
# Keyword category fallback — used when the LLM path is unavailable. Covers
# the real production gap: with recipient/remarks both None (no LLM to
# extract them), every receipt fell straight to "Miscellaneous" with no
# fallback at all, even when the raw OCR text had an obvious keyword match.
# ---------------------------------------------------------------------------


def _llm_unavailable(monkeypatch):
    import brain

    def _boom():
        raise ModuleNotFoundError("No module named 'llama_cpp'")

    monkeypatch.setattr(brain, "_get_llm", _boom)


def test_keyword_classify_category_matches_known_keyword():
    assert expenses._keyword_classify_category("Paid at City Pharmacy for cough syrup") == "Health"


def test_keyword_classify_category_returns_none_without_a_match():
    assert expenses._keyword_classify_category("completely unrelated text") is None


def test_classify_category_falls_back_to_keyword_match_when_llm_unavailable(monkeypatch):
    _llm_unavailable(monkeypatch)

    category = expenses._classify_category(None, None, extra_context="Thimphu Fuel Station\nAmount: 500.00")
    assert category == "Transport"


def test_classify_category_falls_back_to_miscellaneous_when_no_keyword_matches(monkeypatch):
    _llm_unavailable(monkeypatch)

    category = expenses._classify_category(None, None, extra_context="no useful signal here")
    assert category == "Miscellaneous"


def test_classify_category_keyword_fallback_uses_remarks_too(monkeypatch):
    _llm_unavailable(monkeypatch)

    category = expenses._classify_category(None, "beers with friends")
    assert category == "Drinking"
