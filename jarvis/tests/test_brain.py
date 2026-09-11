"""tests/test_brain.py — ask()'s fallback behavior when the LLM is unavailable,
without loading the real GGUF model."""

import brain


def _reset_history():
    brain._history = []


def test_ask_gives_an_honest_reply_when_no_model_is_configured(monkeypatch):
    _reset_history()

    def _boom():
        raise FileNotFoundError("no model file")

    monkeypatch.setattr(brain, "_get_llm", _boom)

    reply = brain.ask("hello")
    assert "don't have a language model running" in reply
    assert "/command" in reply


def test_ask_gives_a_generic_retry_reply_on_other_failures(monkeypatch):
    _reset_history()

    class _FakeLLM:
        def create_chat_completion(self, **kwargs):
            raise RuntimeError("something else broke")

    monkeypatch.setattr(brain, "_get_llm", lambda: _FakeLLM())

    reply = brain.ask("hello")
    assert "Something went wrong on my end" in reply


def test_ask_appends_fallback_reply_to_history_so_pruning_still_works(monkeypatch):
    _reset_history()
    monkeypatch.setattr(brain, "_get_llm", lambda: (_ for _ in ()).throw(FileNotFoundError()))

    brain.ask("hello")
    assert brain._history[-1] == {
        "role": "assistant",
        "content": "I don't have a language model running right now, so I can't chat freely — try a /command instead (see /help).",
    }
