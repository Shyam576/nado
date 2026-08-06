"""tests/test_add_note_intent.py — the "add_note" NL intent dispatch."""

from modules import intent, notes


def test_dispatch_add_note_passes_through_text(monkeypatch):
    captured = {}

    def _fake_add_note(chat_id, args):
        captured["chat_id"] = chat_id
        captured["args"] = args
        return "Noted."

    monkeypatch.setattr(notes, "add_note", _fake_add_note)

    reply = intent._dispatch_add_note("owner", {"text": "the wifi password is on the router"})
    assert reply.text == "Noted."
    assert captured["chat_id"] == "owner"
    assert captured["args"] == ["the", "wifi", "password", "is", "on", "the", "router"]


def test_dispatch_add_note_rejects_empty_text():
    assert intent._dispatch_add_note("owner", {"text": ""}) is None
    assert intent._dispatch_add_note("owner", {}) is None
