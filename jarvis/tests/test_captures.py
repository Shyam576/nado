"""tests/test_captures.py — capture inbox: classification (LLM mocked, same
pattern as test_vision.py), idempotency, correction, status transitions."""

import json

import brain
from modules import captures


def _fake_llm(response: dict):
    """Build a fake brain._get_llm() returning `response` as the classifier's JSON."""

    class _FakeLLM:
        def create_chat_completion(self, **kwargs):
            return {"choices": [{"message": {"content": json.dumps(response)}}]}

    return _FakeLLM()


def _mock_classification(monkeypatch, **fields):
    defaults = {"type": "task", "project": None, "scheduled_for": None, "confidence": 0.9}
    defaults.update(fields)
    monkeypatch.setattr(brain, "_get_llm", lambda: _fake_llm(defaults))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_add_capture_stores_llm_classification(monkeypatch):
    _mock_classification(monkeypatch, type="work_issue", confidence=0.85)

    capture = captures.add_capture(
        "owner", "Investigate payment success without status update", source="telegram", source_message_id="1"
    )

    assert capture["type"] == "work_issue"
    assert capture["status"] == "inbox"
    assert capture["classification_confidence"] == 0.85
    assert capture["classification_model"] is not None
    assert capture["classification_prompt_version"] == "captures-v1"
    assert capture["raw_text"] == "Investigate payment success without status update"


def test_add_capture_extracts_project_and_date(monkeypatch):
    _mock_classification(monkeypatch, type="idea", project="jarvis", scheduled_for="2026-09-26")

    capture = captures.add_capture("owner", "Idea for the jarvis dashboard", source="telegram", source_message_id="2")

    assert capture["project"] == "jarvis"
    assert capture["scheduled_for"] == "2026-09-26"


def test_add_capture_falls_back_to_unknown_on_llm_failure(monkeypatch):
    def _boom():
        raise RuntimeError("model not loaded")

    monkeypatch.setattr(brain, "_get_llm", _boom)

    capture = captures.add_capture("owner", "Something ambiguous", source="telegram", source_message_id="3")

    assert capture["type"] == "unknown"
    assert capture["classification_confidence"] is None
    assert capture["classification_model"] is None
    # The capture is still saved, never lost:
    assert capture["raw_text"] == "Something ambiguous"


def test_add_capture_rejects_invalid_type_from_llm(monkeypatch):
    _mock_classification(monkeypatch, type="not_a_real_type")

    capture = captures.add_capture("owner", "Whatever", source="telegram", source_message_id="4")

    assert capture["type"] == "unknown"


def test_add_capture_ignores_malformed_date_from_llm(monkeypatch):
    _mock_classification(monkeypatch, scheduled_for="not-a-date")

    capture = captures.add_capture("owner", "Whatever", source="telegram", source_message_id="5")

    assert capture["scheduled_for"] is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_add_capture_is_idempotent_on_source_message_id(monkeypatch):
    _mock_classification(monkeypatch)

    first = captures.add_capture("owner", "Buy milk", source="telegram", source_message_id="100")
    second = captures.add_capture("owner", "Buy milk", source="telegram", source_message_id="100")

    assert first["id"] == second["id"]
    assert len(captures.list_captures("owner")) == 1


def test_add_capture_without_source_message_id_is_never_deduped(monkeypatch):
    _mock_classification(monkeypatch)

    captures.add_capture("owner", "Buy milk", source="dashboard")
    captures.add_capture("owner", "Buy milk", source="dashboard")

    assert len(captures.list_captures("owner")) == 2


def test_different_sources_do_not_collide(monkeypatch):
    _mock_classification(monkeypatch)

    captures.add_capture("owner", "Buy milk", source="telegram", source_message_id="1")
    captures.add_capture("owner", "Buy milk", source="discord", source_message_id="1")

    assert len(captures.list_captures("owner")) == 2


# ---------------------------------------------------------------------------
# Correction
# ---------------------------------------------------------------------------


def test_correct_capture_updates_type_without_touching_raw_text(monkeypatch):
    _mock_classification(monkeypatch, type="unknown")
    capture = captures.add_capture("owner", "Learn Kubernetes network policies", source="telegram", source_message_id="6")

    corrected = captures.correct_capture("owner", capture["id"], type_="learning")

    assert corrected["type"] == "learning"
    assert corrected["raw_text"] == "Learn Kubernetes network policies"


def test_correct_capture_rejects_unknown_type(monkeypatch):
    _mock_classification(monkeypatch)
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="7")

    assert captures.correct_capture("owner", capture["id"], type_="not_a_type") is None


def test_correct_capture_rejects_other_owners_capture(monkeypatch):
    _mock_classification(monkeypatch)
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="8")

    assert captures.correct_capture("someone-else", capture["id"], type_="idea") is None


def test_correct_capture_can_clear_project(monkeypatch):
    _mock_classification(monkeypatch, project="jarvis")
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="9")

    cleared = captures.correct_capture("owner", capture["id"], project="")
    assert cleared["project"] is None


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def test_update_status_happy_path(monkeypatch):
    _mock_classification(monkeypatch)
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="10")

    updated = captures.update_status("owner", capture["id"], "completed")
    assert updated["status"] == "completed"


def test_update_status_rejects_unknown_status(monkeypatch):
    _mock_classification(monkeypatch)
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="11")

    try:
        captures.update_status("owner", capture["id"], "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_update_status_returns_none_for_other_owner(monkeypatch):
    _mock_classification(monkeypatch)
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="12")

    assert captures.update_status("someone-else", capture["id"], "completed") is None


def test_schedule_capture_rejects_malformed_date(monkeypatch):
    _mock_classification(monkeypatch)
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="13")

    assert captures.schedule_capture("owner", capture["id"], "not-a-date") is None


def test_schedule_capture_happy_path(monkeypatch):
    _mock_classification(monkeypatch)
    capture = captures.add_capture("owner", "Something", source="telegram", source_message_id="14")

    scheduled = captures.schedule_capture("owner", capture["id"], "2026-10-01")
    assert scheduled["scheduled_for"] == "2026-10-01"


# ---------------------------------------------------------------------------
# Listing / filtering
# ---------------------------------------------------------------------------


def test_list_captures_filters_by_status(monkeypatch):
    _mock_classification(monkeypatch)
    a = captures.add_capture("owner", "First", source="telegram", source_message_id="20")
    captures.add_capture("owner", "Second", source="telegram", source_message_id="21")
    captures.update_status("owner", a["id"], "archived")

    inbox_only = captures.list_captures("owner", status="inbox")
    assert len(inbox_only) == 1
    assert inbox_only[0]["raw_text"] == "Second"


def test_list_captures_scoped_to_owner(monkeypatch):
    _mock_classification(monkeypatch)
    captures.add_capture("owner", "Mine", source="telegram", source_message_id="30")
    captures.add_capture("someone-else", "Theirs", source="telegram", source_message_id="31")

    assert [c["raw_text"] for c in captures.list_captures("owner")] == ["Mine"]


# ---------------------------------------------------------------------------
# Bot command entry point
# ---------------------------------------------------------------------------


def test_handle_capture_command_add(monkeypatch):
    _mock_classification(monkeypatch, type="idea")

    reply = captures.handle_capture_command(
        "owner", ["Idea", "for", "automating", "reports"], source="telegram", source_message_id="40"
    )

    assert "Captured" in reply
    assert "idea" in reply


def test_handle_capture_command_no_args_shows_usage():
    assert "Usage" in captures.handle_capture_command("owner", [])


def test_handle_capture_command_list(monkeypatch):
    _mock_classification(monkeypatch)
    captures.handle_capture_command("owner", ["Buy", "milk"], source="telegram", source_message_id="41")

    reply = captures.handle_capture_command("owner", ["list"])
    assert "Buy milk" in reply


def test_handle_capture_command_done(monkeypatch):
    _mock_classification(monkeypatch)
    add_reply = captures.handle_capture_command("owner", ["Buy", "milk"], source="telegram", source_message_id="42")
    capture_id = add_reply.split("#")[1].split(")")[0]

    reply = captures.handle_capture_command("owner", ["done", capture_id])
    assert "Captured" in reply

    done_only = captures.list_captures("owner", status="completed")
    assert len(done_only) == 1
