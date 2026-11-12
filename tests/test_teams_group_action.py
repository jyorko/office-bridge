import pytest
from actions import ActionError

import teams_actions as actions
from office_bridge import desktop


def setup_send(tmp_path, monkeypatch, invoke):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(desktop, "invoke", invoke)


def test_observed_receipt_is_reused_without_another_send(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "observed",
            "message_id": "message-body-123",
            "time": "2026-11-12T19:00:00Z",
        }

    setup_send(tmp_path, monkeypatch, invoke)
    first = actions.teams_web_send_qa_chat("test", "test-1")
    second = actions.teams_web_send_qa_chat("test", "test-1")
    assert first.result.status == "observed"
    assert second.result.status == "already_observed"
    assert len(calls) == 1
    with pytest.raises(ActionError):
        actions.teams_web_send_qa_chat("different", "test-1")


def test_uncertain_send_cannot_be_retried(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        raise RuntimeError("unknown")

    setup_send(tmp_path, monkeypatch, invoke)
    for _ in range(2):
        with pytest.raises(ActionError):
            actions.teams_web_send_qa_chat("test", "test-2")
    assert len(calls) == 1
