"""Synthetic Teams file-upload checks; never contact Teams or upload a file."""

import sqlite3

import pytest
from actions import ActionError

import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.models import WebChatRef

CHAT = WebChatRef(
    chat_id="19:" + "a" * 32 + "@thread.v2",
    title="Verification Group",
)


def _readiness(chat=CHAT, *, ready=True):
    return {
        "state": "ready" if ready else "blocked",
        "chat_id": chat.chat_id,
        "title": chat.title,
        "composer_empty": ready,
        "file_input_available": ready,
        "file_input_count": 1 if ready else 0,
        "file_input_accepts": ".pdf,.doc,.docx",
        "reason": None if ready else "composer_has_draft",
    }


def setup_upload(tmp_path, monkeypatch, invoke):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("OFFICE_BRIDGE_ACCOUNT", "person@example.invalid")
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "legacy-self-ledger.json")
    monkeypatch.setattr(desktop, "invoke", invoke)


def test_upload_reserves_exact_files_and_replays_only_cached_receipt(tmp_path, monkeypatch):
    path = tmp_path / "evaluation.pdf"
    path.write_bytes(b"synthetic evaluation")
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        if operation == "teams_web_upload_readiness":
            return _readiness()
        return {
            "state": "uploaded",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": "message-body-1234567890123",
            "time": "2026-11-17T15:00:00Z",
            "files": ["evaluation.pdf"],
        }

    setup_upload(tmp_path, monkeypatch, invoke)
    first = actions.teams_web_upload_files(CHAT, [str(path)], "upload-1")
    second = actions.teams_web_upload_files(CHAT, [str(path)], "upload-1")

    assert first.result.status == "uploaded"
    assert first.result.files == ["evaluation.pdf"]
    assert second.result.status == "already_uploaded"
    assert [operation for operation, _ in calls] == [
        "teams_web_upload_readiness",
        "teams_web_upload_files",
    ]
    native_files = calls[1][1]["files"]
    assert native_files[0]["name"] == "evaluation.pdf"
    assert native_files[0]["size_bytes"] == path.stat().st_size
    assert len(native_files[0]["sha256"]) == 64
    assert str(path) == native_files[0]["path"]

    database = tmp_path / "OfficeActionServer/teams-web-sends/group.sqlite3"
    with sqlite3.connect(database) as db:
        row = db.execute(
            "SELECT chat_id,chat_title,digest,state,file_count,receipt FROM uploads WHERE id=?",
            ("upload-1",),
        ).fetchone()
    assert row[:2] == (CHAT.chat_id, CHAT.title)
    assert len(row[2]) == 64 and row[3:5] == ("uploaded", 1)
    assert str(path) not in (row[5] or "")


def test_unknown_upload_is_never_replayed_and_status_does_not_click(tmp_path, monkeypatch):
    path = tmp_path / "evaluation.pdf"
    path.write_bytes(b"synthetic evaluation")
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        if operation == "teams_web_upload_readiness":
            return _readiness()
        if operation == "teams_web_upload_files":
            raise OSError("simulated lost response after submission")
        return {"state": "unknown"}

    setup_upload(tmp_path, monkeypatch, invoke)
    for _ in range(2):
        with pytest.raises(ActionError, match="unknown"):
            actions.teams_web_upload_files(CHAT, [str(path)], "upload-unknown")
    status = actions.teams_web_upload_status(CHAT, "upload-unknown")

    assert status.result.status == "outcome_unknown"
    assert status.result.file_count == 1
    assert calls == [
        "teams_web_upload_readiness",
        "teams_web_upload_files",
        "teams_web_upload_status",
    ]


def test_preexisting_composer_and_wrong_chat_receipt_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "evaluation.pdf"
    path.write_bytes(b"synthetic evaluation")
    calls = []

    def blocked(operation, arguments):
        calls.append(operation)
        return _readiness(ready=False)

    setup_upload(tmp_path, monkeypatch, blocked)
    with pytest.raises(ActionError, match="not ready"):
        actions.teams_web_upload_files(CHAT, [str(path)], "upload-draft")
    assert calls == ["teams_web_upload_readiness"]
    assert not (tmp_path / "OfficeActionServer/teams-web-sends/group.sqlite3").exists()

    other = WebChatRef(
        chat_id="19:" + "b" * 32 + "@thread.v2",
        title=CHAT.title,
    )

    def wrong_receipt(operation, arguments):
        calls.append(operation)
        if operation == "teams_web_upload_readiness":
            return _readiness()
        return {
            "state": "uploaded",
            "chat_id": other.chat_id,
            "title": other.title,
            "message_id": "message-body-1234567890123",
            "time": "2026-11-17T15:00:00Z",
            "files": ["evaluation.pdf"],
        }

    setup_upload(tmp_path, monkeypatch, wrong_receipt)
    with pytest.raises(ActionError, match="unknown"):
        actions.teams_web_upload_files(CHAT, [str(path)], "upload-wrong-chat")
    with sqlite3.connect(tmp_path / "OfficeActionServer/teams-web-sends/group.sqlite3") as db:
        state = db.execute("SELECT state FROM uploads WHERE id='upload-wrong-chat'").fetchone()[0]
    assert state == "reserved"


def test_file_path_name_and_size_checks_happen_before_native_call(tmp_path, monkeypatch):
    calls = []
    setup_upload(tmp_path, monkeypatch, lambda operation, args: calls.append(operation))
    with pytest.raises(ActionError):
        actions.teams_web_upload_files(CHAT, ["relative.pdf"], "upload-relative")
    assert calls == []

    duplicate_a = tmp_path / "first" / "same.pdf"
    duplicate_b = tmp_path / "second" / "same.pdf"
    duplicate_a.parent.mkdir()
    duplicate_b.parent.mkdir()
    duplicate_a.write_bytes(b"one")
    duplicate_b.write_bytes(b"two")
    with pytest.raises(ActionError, match="unique"):
        actions.teams_web_upload_files(
            CHAT, [str(duplicate_a), str(duplicate_b)], "upload-duplicate-name"
        )
    assert calls == []

    too_large = tmp_path / "too-large.pdf"
    with too_large.open("wb") as stream:
        stream.truncate(25 * 1024 * 1024 + 1)
    with pytest.raises(ActionError, match="25 MiB"):
        actions.teams_web_upload_files(CHAT, [str(too_large)], "upload-too-large")
    assert calls == []


def test_upload_request_id_cannot_reuse_retired_send_or_another_chat(tmp_path, monkeypatch):
    path = tmp_path / "evaluation.pdf"
    path.write_bytes(b"synthetic evaluation")
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        if operation == "teams_web_upload_readiness":
            return _readiness()
        return {
            "state": "uploaded",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": "message-body-1234567890123",
            "time": "2026-11-17T15:00:00Z",
            "files": ["evaluation.pdf"],
        }

    setup_upload(tmp_path, monkeypatch, invoke)
    ledger.send_once("retired-id", "old body", lambda: {"status": "outcome_unknown"})
    with pytest.raises(ActionError, match="retired send ledger"):
        actions.teams_web_upload_files(CHAT, [str(path)], "retired-id")
    actions.teams_web_upload_files(CHAT, [str(path)], "upload-destination")

    other = WebChatRef(
        chat_id="19:" + "b" * 32 + "@thread.v2",
        title="Different Group",
    )
    with pytest.raises(ActionError, match="conflicts"):
        actions.teams_web_upload_files(other, [str(path)], "upload-destination")
    assert calls == ["teams_web_upload_readiness", "teams_web_upload_files"]
