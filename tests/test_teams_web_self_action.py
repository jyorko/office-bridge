import pytest
from actions import ActionError

import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams import ledger


def test_self_message_is_sent_once_per_request(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "self-ledger.json")
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {"state": "observed", "message_id": "message-body-1794510375584"}

    monkeypatch.setattr(desktop, "invoke", invoke)
    first = actions.teams_web_send_self("hello", "self-1")
    second = actions.teams_web_send_self("hello", "self-1")
    assert first.result.status == "observed"
    assert second.result.status == "already_observed"
    assert len(calls) == 1
    assert calls[0] == ("teams_web_send_self", {"body": "hello", "request_id": "self-1"})


def test_unknown_self_send_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "self-ledger.json")
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        raise desktop.BridgeError("mutation_outcome_unknown")

    monkeypatch.setattr(desktop, "invoke", invoke)
    for _ in range(2):
        with pytest.raises(ActionError):
            actions.teams_web_send_self("hello", "self-2")
    assert calls == ["teams_web_send_self"]
