import pytest
from actions import ActionError

import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.models import WebChatRef

CHAT_ID = "19:" + "a" * 32 + "@thread.v2"
CHAT = WebChatRef(chat_id=CHAT_ID, title="Example Group")


def setup_send(tmp_path, monkeypatch, invoke):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("OFFICE_TEAMS_EXPECTED_ACCOUNT", "person@example.invalid")
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "legacy-self-ledger.json")
    monkeypatch.setattr(desktop, "invoke", invoke)


def test_observed_receipt_reuses_only_same_destination(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "observed",
            "message_id": "message-body-123",
            "time": "2026-11-12T19:00:00Z",
        }

    setup_send(tmp_path, monkeypatch, invoke)
    first = actions.teams_web_send_chat(CHAT, "test", "test-1")
    second = actions.teams_web_send_chat(CHAT, "test", "test-1")
    assert first.result.status == "observed"
    assert second.result.status == "already_observed"
    assert second.result.chat == CHAT
    assert calls == [
        (
            "teams_web_send_chat",
            {"chat": CHAT.model_dump(mode="json"), "body": "test", "request_id": "test-1"},
        )
    ]
    other = WebChatRef(chat_id="19:" + "b" * 32 + "@thread.v2", title="Another Group")
    with pytest.raises(ActionError):
        actions.teams_web_send_chat(other, "test", "test-1")


def test_uncertain_send_cannot_be_retried(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        raise RuntimeError("unknown")

    setup_send(tmp_path, monkeypatch, invoke)
    for _ in range(2):
        with pytest.raises(ActionError):
            actions.teams_web_send_chat(CHAT, "test", "test-2")
    assert calls == ["teams_web_send_chat"]


def test_legacy_self_send_id_is_never_reused(tmp_path, monkeypatch):
    setup_send(tmp_path, monkeypatch, lambda *_: pytest.fail("must not submit"))
    ledger.send_once(
        "legacy-1",
        "old body",
        lambda: {"status": "outcome_unknown", "send_clicked": False},
    )
    with pytest.raises(ActionError):
        actions.teams_web_send_chat(CHAT, "new body", "legacy-1")
