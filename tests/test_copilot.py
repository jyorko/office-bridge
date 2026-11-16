import json

import pytest

from office_bridge.teams import copilot


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "OfficeActionServer/copilot-queries"


def ready():
    return {"state": "ready", "work_iq_enabled": True, "composer_nonempty": False}


def test_same_question_id_never_submits_twice(store, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        return (
            ready()
            if operation.endswith("status")
            else {"state": "clicked", "request_id": "test-1"}
        )

    monkeypatch.setattr(copilot, "_invoke", invoke)
    receipt = copilot.ask("Find project decisions", "test-1")
    assert receipt.status == "awaiting_response" and receipt.submission_verified is False
    assert copilot.ask("Find project decisions", "test-1") == receipt
    assert calls.count("teams_copilot_ask") == 1
    with pytest.raises(copilot.CopilotError, match="another question"):
        copilot.ask("A different question", "test-1")


def test_lost_submission_response_is_not_replayed(store, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        if operation.endswith("status"):
            return ready()
        raise copilot.CopilotError("connection lost")

    monkeypatch.setattr(copilot, "_invoke", invoke)
    with pytest.raises(copilot.CopilotError, match="connection lost"):
        copilot.ask("Find project decisions", "test-2")
    with pytest.raises(copilot.CopilotError, match="uncertain"):
        copilot.ask("Find project decisions", "test-2")
    assert calls.count("teams_copilot_ask") == 1


def test_existing_draft_blocks_question_before_reservation(store, monkeypatch):
    monkeypatch.setattr(copilot, "_invoke", lambda *_: {**ready(), "composer_nonempty": True})
    with pytest.raises(copilot.CopilotError, match="existing draft"):
        copilot.ask("Find project decisions", "test-3")
    assert not list(store.glob("*.json"))


def test_completed_result_is_cached_with_sources_and_never_reasks(store, monkeypatch):
    store.mkdir(parents=True)
    (store / "query-test-4.json").write_text(
        json.dumps({"question": "Question", "state": "reserved"})
    )
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        return {
            "state": "completed",
            "submission_verified": True,
            "work_iq_enabled": True,
            "answer": "Observed answer",
            "citations": [{"title": "Source", "url": "https://example.invalid/source"}],
        }

    monkeypatch.setattr(copilot, "_invoke", invoke)
    first = copilot.read("test-4")
    assert first.answer == "Observed answer" and len(first.citations) == 1
    assert copilot.read("test-4") == first
    assert calls == ["teams_copilot_read"]


def test_unverified_question_cannot_be_reported_completed(store, monkeypatch):
    store.mkdir(parents=True)
    (store / "query-test-5.json").write_text(json.dumps({"question": "Question"}))
    monkeypatch.setattr(
        copilot,
        "_invoke",
        lambda *_: {
            "state": "completed",
            "answer": "Answer",
            "submission_verified": False,
            "work_iq_enabled": True,
        },
    )
    with pytest.raises(copilot.CopilotError, match="could not be verified"):
        copilot.read("test-5")


def test_only_proven_unsubmitted_matching_draft_can_continue(store, monkeypatch):
    store.mkdir(parents=True)
    (store / "query-test-6.json").write_text(
        json.dumps({"question": "Question", "state": "reserved"})
    )
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        if operation.endswith("read"):
            return {"state": "not_submitted", "question_matches": True, "work_iq_enabled": True}
        return {"state": "clicked", "request_id": "test-6"}

    monkeypatch.setattr(copilot, "_invoke", invoke)
    assert copilot.read("test-6").status == "not_submitted"
    assert copilot.ask("Question", "test-6").status == "awaiting_response"
    assert calls == ["teams_copilot_read", "teams_copilot_read", "teams_copilot_ask"]


def test_changed_unsubmitted_draft_cannot_continue(store, monkeypatch):
    store.mkdir(parents=True)
    (store / "query-test-7.json").write_text(
        json.dumps({"question": "Question", "state": "not_submitted"})
    )
    monkeypatch.setattr(
        copilot,
        "_invoke",
        lambda *_: {"state": "not_submitted", "question_matches": False, "work_iq_enabled": True},
    )
    with pytest.raises(copilot.CopilotError, match="draft changed"):
        copilot.ask("Question", "test-7")


def test_model_choice_is_verified_and_bound_to_request(store, monkeypatch):
    submitted = []

    def invoke(operation, arguments):
        if operation.endswith("status"):
            return ready()
        if operation.endswith("models"):
            return {
                "state": "models",
                "model": "Example",
                "models": [{"name": "Alternative", "selected": False, "enabled": True}],
            }
        submitted.append(arguments)
        return {"state": "clicked", "request_id": "model-test", "model": "Alternative"}

    monkeypatch.setattr(copilot, "_invoke", invoke)
    receipt = copilot.ask("Question", "model-test", "Alternative")
    assert receipt.model == "Alternative"
    assert submitted == [
        {"question": "Question", "request_id": "model-test", "model": "Alternative"}
    ]
    with pytest.raises(copilot.CopilotError, match="another question"):
        copilot.ask("Question", "model-test", "Example")


def test_disabled_model_is_rejected_before_reservation(store, monkeypatch):
    def invoke(operation, arguments):
        if operation.endswith("status"):
            return ready()
        assert operation.endswith("models")
        return {
            "state": "models",
            "model": "Example",
            "models": [{"name": "Unavailable", "selected": False, "enabled": False}],
        }

    monkeypatch.setattr(copilot, "_invoke", invoke)
    with pytest.raises(copilot.CopilotError, match="available enabled"):
        copilot.ask("Question", "disabled-test", "Unavailable")
    assert not list(store.glob("*.json"))


def test_select_model_verifies_choice_without_question_submission(monkeypatch):
    calls = []

    def invoke(operation, arguments):
        assert operation == "teams_copilot_models"
        calls.append(arguments)
        selected = "model" in arguments
        return {
            "state": "models",
            "model": "Alternative" if selected else "Example",
            "models": [{"name": "Alternative", "selected": selected, "enabled": True}],
        }

    monkeypatch.setattr(copilot, "_invoke", invoke)
    result = copilot.select_model("Alternative")
    assert result.model == "Alternative"
    assert calls == [{}, {"model": "Alternative"}]
