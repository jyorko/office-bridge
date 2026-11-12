import json

import pytest

from office_bridge.teams import ledger


def use_tmp_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_BRIDGE_ACCOUNT", "person@example.invalid")
    path = tmp_path / "OfficeActionServer" / "teams-web-sends" / "ledger.json"
    monkeypatch.setattr(ledger, "LEDGER_PATH", path)
    return path


def test_observed_send_is_durable_and_replay_does_not_submit(tmp_path, monkeypatch):
    path = use_tmp_ledger(tmp_path, monkeypatch)
    calls = []

    def submit():
        calls.append(1)
        saved = json.loads(path.read_text(encoding="utf-8"))
        entry = saved["entries"]["case-1"]
        assert entry["status"] == "attempted"
        assert "private body" not in path.read_text(encoding="utf-8")
        return {
            "status": "observed",
            "send_clicked": True,
            "message_id": "msg-123",
            "recipient": ledger.expected_recipient(),
            "account_verified": True,
            "body": "must not persist",
            "debug": "secret",
        }

    first = ledger.send_once("case-1", "private body", submit)
    second = ledger.send_once("case-1", "private body", submit)
    assert first == {
        "status": "observed",
        "message_id": "msg-123",
        "recipient": ledger.expected_recipient(),
        "account_verified": True,
        "send_clicked": True,
    }
    assert second["status"] == "already_observed"
    assert second["message_id"] == "msg-123"
    assert calls == [1]
    saved = path.read_text(encoding="utf-8")
    assert "private body" not in saved
    assert "must not persist" not in saved
    assert "secret" not in saved


def test_same_key_with_different_body_is_conflict(tmp_path, monkeypatch):
    use_tmp_ledger(tmp_path, monkeypatch)
    ledger.send_once("same", "one", lambda: {"status": "outcome_unknown"})
    with pytest.raises(ledger.SendLedgerError) as exc:
        ledger.send_once("same", "two", lambda: pytest.fail("must not submit"))
    assert str(exc.value) == "idempotency_conflict"


def test_callback_exception_is_unknown_and_never_retried(tmp_path, monkeypatch):
    use_tmp_ledger(tmp_path, monkeypatch)
    calls = []

    def fail():
        calls.append(1)
        raise RuntimeError("sensitive message body")

    with pytest.raises(ledger.SendLedgerError) as exc:
        ledger.send_once("key", "body", fail)
    assert str(exc.value) == "send_outcome_unknown"
    with pytest.raises(ledger.SendLedgerError) as exc:
        ledger.send_once("key", "body", lambda: calls.append(2))
    assert str(exc.value) == "send_outcome_unknown"
    assert calls == [1]


def test_only_explicit_untouched_not_sent_allows_later_explicit_call(tmp_path, monkeypatch):
    use_tmp_ledger(tmp_path, monkeypatch)
    calls = []

    def proven_preflight():
        calls.append("preflight")
        return {"status": "not_sent", "send_clicked": False, "composer_unchanged": True}

    assert ledger.send_once("key", "body", proven_preflight)["status"] == "not_sent"
    observed = ledger.send_once(
        "key",
        "body",
        lambda: calls.append("submit")
        or {
            "status": "observed",
            "send_clicked": True,
            "message_id": "id-1",
        },
    )
    assert observed["status"] == "observed"
    assert calls == ["preflight", "submit"]


@pytest.mark.parametrize(
    "result",
    [
        {"status": "outcome_unknown", "send_clicked": False},
        {"status": "not_sent", "send_clicked": True, "composer_unchanged": True},
        {"status": "not_sent", "send_clicked": False, "composer_unchanged": False},
        {"status": "observed", "send_clicked": True},
        {"status": "observed", "send_clicked": True, "message_id": "id", "account_verified": False},
        {
            "status": "observed",
            "send_clicked": True,
            "message_id": "id",
            "recipient": "someone-else@example.com",
        },
    ],
)
def test_unproven_results_fail_closed(tmp_path, monkeypatch, result):
    use_tmp_ledger(tmp_path, monkeypatch)
    calls = []
    assert (
        ledger.send_once("key", "body", lambda: calls.append(1) or result)["status"]
        == "outcome_unknown"
    )
    with pytest.raises(ledger.SendLedgerError) as exc:
        ledger.send_once("key", "body", lambda: calls.append(2))
    assert str(exc.value) == "send_outcome_unknown"
    assert calls == [1]


def test_corrupt_ledger_fails_closed_without_calling_submit(tmp_path, monkeypatch):
    path = use_tmp_ledger(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ledger.SendLedgerError) as exc:
        ledger.send_once("key", "body", lambda: pytest.fail("must not submit"))
    assert str(exc.value) == "ledger_corrupt"
    assert path.read_text(encoding="utf-8") == "{broken"


def test_key_validation_happens_before_ledger_creation(tmp_path, monkeypatch):
    path = use_tmp_ledger(tmp_path, monkeypatch)
    with pytest.raises(ledger.SendLedgerError) as exc:
        ledger.send_once("unsafe/key", "body", lambda: None)
    assert str(exc.value) == "invalid_idempotency_key"
    assert not path.exists()
