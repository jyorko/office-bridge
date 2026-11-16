import sqlite3

import pytest
from actions import ActionError

import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.models import WebChatRef

CHAT_ID = "19:" + "a" * 32 + "@thread.v2"
CHAT = WebChatRef(chat_id=CHAT_ID, title="Example Group")
MESSAGE_ID = "message-body-1234567890123"
OLD_TEXT = "before edit"


def setup_edit(tmp_path, monkeypatch, invoke):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("OFFICE_BRIDGE_ACCOUNT", "person@example.invalid")
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "legacy-self-ledger.json")
    monkeypatch.setattr(desktop, "invoke", invoke)


def _inspect(chat=CHAT, message_id=MESSAGE_ID, text=OLD_TEXT, own=True):
    return {
        "state": "inspected",
        "chat_id": chat.chat_id,
        "title": chat.title,
        "message_id": message_id,
        "own_message": own,
        "text": text,
    }


def test_edit_reserves_before_native_and_replays_only_cached_exact_receipt(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        if operation == "teams_web_inspect_message":
            return _inspect()
        return {
            "state": "edited",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": MESSAGE_ID,
            "time": "2026-11-12T19:00:00Z",
        }

    setup_edit(tmp_path, monkeypatch, invoke)
    first = actions.teams_web_edit_message(
        CHAT, MESSAGE_ID, OLD_TEXT, "**after**", "edit-1", "markdown"
    )
    assert first.result.status == "edited"
    assert [call[0] for call in calls] == ["teams_web_inspect_message", "teams_web_edit_message"]
    native = calls[1][1]
    assert native["chat"] == CHAT.model_dump(mode="json")
    assert native["expected_text"] == OLD_TEXT
    assert native["body"] == "after"
    assert native["body_format"] == "markdown"
    assert native["formatting"]["bold"] == ["after"]

    replay = actions.teams_web_edit_message(
        CHAT, MESSAGE_ID, OLD_TEXT, "**after**", "edit-1", "markdown"
    )
    assert replay.result.status == "already_edited"
    assert [call[0] for call in calls] == ["teams_web_inspect_message", "teams_web_edit_message"]
    with pytest.raises(ActionError):
        actions.teams_web_edit_message(CHAT, MESSAGE_ID, OLD_TEXT, "after", "edit-1", "plain")
    assert [call[0] for call in calls] == ["teams_web_inspect_message", "teams_web_edit_message"]

    database = tmp_path / "OfficeActionServer/teams-web-sends/group.sqlite3"
    with sqlite3.connect(database) as db:
        row = db.execute(
            "SELECT chat_id,message_id,expected_sha256,body_format,digest,state FROM edits WHERE id=?",
            ("edit-1",),
        ).fetchone()
    assert row[:2] == (CHAT.chat_id, MESSAGE_ID)
    assert len(row[2]) == 64 and row[3] == "markdown" and len(row[4]) == 64
    assert row[5] == "edited"


def test_wrong_or_ambiguous_preflight_never_reserves_or_edits(tmp_path, monkeypatch):
    calls = []
    setup_edit(
        tmp_path,
        monkeypatch,
        lambda operation, arguments: calls.append(operation) or _inspect(text="not exact"),
    )
    with pytest.raises(ActionError):
        actions.teams_web_edit_message(CHAT, MESSAGE_ID, OLD_TEXT, "after", "edit-mismatch")
    assert calls == ["teams_web_inspect_message"]
    assert not (tmp_path / "OfficeActionServer/teams-web-sends/group.sqlite3").exists()


def test_non_owner_preflight_never_reserves_or_edits(tmp_path, monkeypatch):
    calls = []
    setup_edit(
        tmp_path,
        monkeypatch,
        lambda operation, arguments: calls.append(operation) or _inspect(own=False),
    )
    with pytest.raises(ActionError):
        actions.teams_web_edit_message(CHAT, MESSAGE_ID, OLD_TEXT, "after", "edit-not-owner")
    assert calls == ["teams_web_inspect_message"]


def test_unknown_edit_reservation_cannot_be_retried(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        if operation == "teams_web_inspect_message":
            return _inspect()
        raise RuntimeError("unknown native result")

    setup_edit(tmp_path, monkeypatch, invoke)
    for _ in range(2):
        with pytest.raises(ActionError):
            actions.teams_web_edit_message(CHAT, MESSAGE_ID, OLD_TEXT, "after", "edit-unknown")
    assert calls == ["teams_web_inspect_message", "teams_web_edit_message"]
    database = tmp_path / "OfficeActionServer/teams-web-sends/group.sqlite3"
    with sqlite3.connect(database) as db:
        state = db.execute("SELECT state FROM edits WHERE id='edit-unknown'").fetchone()[0]
    assert state == "reserved"


def test_request_id_cannot_reuse_receipt_for_another_chat(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        if operation == "teams_web_inspect_message":
            return _inspect()
        return {
            "state": "edited",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": MESSAGE_ID,
            "time": "2026-11-12T19:00:00Z",
        }

    setup_edit(tmp_path, monkeypatch, invoke)
    actions.teams_web_edit_message(CHAT, MESSAGE_ID, OLD_TEXT, "after", "edit-target")
    other = WebChatRef(chat_id="19:" + "b" * 32 + "@thread.v2", title="Other Group")
    with pytest.raises(ActionError):
        actions.teams_web_edit_message(other, MESSAGE_ID, OLD_TEXT, "after", "edit-target")
    assert calls == ["teams_web_inspect_message", "teams_web_edit_message"]


def test_native_receipt_for_wrong_message_leaves_reservation_blocked(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        if operation == "teams_web_inspect_message":
            return _inspect()
        return {
            "state": "edited",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": "message-body-1234567890124",
            "time": "2026-11-12T19:00:00Z",
        }

    setup_edit(tmp_path, monkeypatch, invoke)
    for _ in range(2):
        with pytest.raises(ActionError):
            actions.teams_web_edit_message(CHAT, MESSAGE_ID, OLD_TEXT, "after", "bad-receipt")
    assert calls == ["teams_web_inspect_message", "teams_web_edit_message"]


def test_edit_rejects_retired_self_send_request_id(tmp_path, monkeypatch):
    setup_edit(tmp_path, monkeypatch, lambda *_: pytest.fail("must not inspect or edit"))
    ledger.send_once("legacy-edit", "old", lambda: {"status": "outcome_unknown"})
    with pytest.raises(ActionError):
        actions.teams_web_edit_message(CHAT, MESSAGE_ID, OLD_TEXT, "after", "legacy-edit")
