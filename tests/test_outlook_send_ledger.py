"""Outlook send ledger tests use temporary private-state paths and fake submitters."""

import pytest

from office_bridge import desktop
from office_bridge.outlook import ledger, service
from office_bridge.outlook.models import MessageRef


@pytest.fixture
def private_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger, "_root", lambda: tmp_path)
    return tmp_path


def verified_result(draft):
    return {
        "state": "sent_verified",
        "account_verified": True,
        "sent_items_verified": True,
        "draft": draft,
        "submitted_at": "2026-11-18T15:00:00+00:00",
        "subject": "Synthetic subject",
        "body_text": "Synthetic body",
        "body_truncated": False,
    }


def test_send_is_reserved_once_and_verified_receipt_is_cached(private_ledger):
    draft = {"entry_id": "web-draft-1", "store_id": "web-store-1"}
    calls = []

    def submit():
        calls.append(True)
        return verified_result(draft)

    receipt = ledger.send_once(draft, "a" * 64, submit)
    repeated = ledger.send_once(
        draft,
        "a" * 64,
        lambda: pytest.fail("a verified send must not be submitted again"),
    )
    assert receipt["status"] == "submitted"
    assert repeated["status"] == "already_submitted"
    assert receipt["draft"] == draft
    assert receipt["subject"] == repeated["subject"] == "Synthetic subject"
    assert receipt["body_text"] == repeated["body_text"] == "Synthetic body"
    assert receipt["account_verified"] is True
    assert receipt["sent_items_verified"] is True
    assert len(calls) == 1


def test_uncertain_send_is_durably_blocked_from_replay(private_ledger):
    draft = {"entry_id": "web-draft-2", "store_id": "web-store-1"}
    calls = []

    def uncertain():
        calls.append(True)
        raise TimeoutError

    with pytest.raises(ledger.SendLedgerError, match="mutation_outcome_unknown"):
        ledger.send_once(draft, "b" * 64, uncertain)
    with pytest.raises(ledger.SendLedgerError, match="mutation_outcome_unknown"):
        ledger.send_once(
            draft,
            "b" * 64,
            lambda: pytest.fail("an uncertain send must never be replayed"),
        )
    assert calls == [True]


def test_same_draft_cannot_be_reused_with_changed_digest(private_ledger):
    draft = {"entry_id": "web-draft-3", "store_id": "web-store-1"}
    ledger.send_once(draft, "c" * 64, lambda: verified_result(draft))
    with pytest.raises(ledger.SendLedgerError, match="idempotency_conflict"):
        ledger.send_once(
            draft,
            "d" * 64,
            lambda: pytest.fail("a changed draft digest must not be submitted"),
        )


def test_proven_preclick_failure_does_not_burn_draft_or_retry_automatically(private_ledger):
    draft = {"entry_id": "web-draft-preflight", "store_id": "web-store-1"}
    calls = []
    not_sent = {
        "state": "not_sent",
        "account_verified": True,
        "preflight_verified": True,
        "send_clicked": False,
        "draft_unchanged": True,
        "sent_items_verified": False,
        "draft": draft,
        "expected_digest": "f" * 64,
    }

    def proven_preflight_failure():
        calls.append("preflight")
        return not_sent

    with pytest.raises(ledger.SendLedgerError, match="send_not_submitted"):
        ledger.send_once(draft, "f" * 64, proven_preflight_failure)
    assert calls == ["preflight"]

    receipt = ledger.send_once(
        draft, "0" * 64, lambda: (calls.append("submit") or verified_result(draft))
    )
    assert receipt["status"] == "submitted"
    assert calls == ["preflight", "submit"]


def test_unimplemented_send_fails_before_it_reserves_a_draft(monkeypatch):
    draft = {"entry_id": "web-draft-unimplemented", "store_id": "web-store-1"}
    monkeypatch.setattr(desktop, "OUTLOOK_SUPPORTED", frozenset({"inbox_list"}))
    monkeypatch.setattr(
        ledger,
        "send_once",
        lambda *args, **kwargs: pytest.fail("unsupported send must not reserve"),
    )
    with pytest.raises(desktop.BridgeError, match="outlook_operation_unavailable"):
        service.send_draft(MessageRef.model_validate(draft), "a" * 64)


def test_unproven_not_sent_result_remains_terminal_unknown(private_ledger):
    draft = {"entry_id": "web-draft-unproven", "store_id": "web-store-1"}
    unproven = {
        "state": "not_sent",
        "account_verified": True,
        "preflight_verified": False,
        "send_clicked": False,
        "draft_unchanged": True,
        "sent_items_verified": False,
    }
    with pytest.raises(ledger.SendLedgerError, match="mutation_outcome_unknown"):
        ledger.send_once(draft, "1" * 64, lambda: unproven)
    with pytest.raises(ledger.SendLedgerError, match="mutation_outcome_unknown"):
        ledger.send_once(
            draft,
            "1" * 64,
            lambda: pytest.fail("an unproven pre-click result cannot be replayed"),
        )


@pytest.mark.parametrize("mismatch", ["draft", "expected_digest"])
def test_preclick_proof_must_match_request_before_recovery(private_ledger, mismatch):
    draft = {"entry_id": "web-draft-proof", "store_id": "web-store-1"}
    proof = {
        "state": "not_sent",
        "account_verified": True,
        "preflight_verified": True,
        "send_clicked": False,
        "draft_unchanged": True,
        "sent_items_verified": False,
        "draft": draft,
        "expected_digest": "2" * 64,
    }
    proof[mismatch] = (
        {"entry_id": "other-draft", "store_id": "web-store-1"} if mismatch == "draft" else "3" * 64
    )
    with pytest.raises(ledger.SendLedgerError, match="mutation_outcome_unknown"):
        ledger.send_once(draft, "2" * 64, lambda: proof)
    with pytest.raises(ledger.SendLedgerError, match="mutation_outcome_unknown"):
        ledger.send_once(draft, "2" * 64, lambda: pytest.fail("must not retry"))


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"state": "sent_verified", "account_verified": False, "sent_items_verified": True},
        {"state": "sent_verified", "account_verified": True, "sent_items_verified": False},
        {
            "state": "sent_verified",
            "account_verified": True,
            "sent_items_verified": True,
            "draft": {"entry_id": "other", "store_id": "web-store-1"},
            "submitted_at": "2026-11-18T15:00:00+00:00",
        },
    ],
)
def test_unverified_sent_items_result_is_unknown_and_not_replayed(private_ledger, result):
    draft = {"entry_id": "web-draft-4", "store_id": "web-store-1"}
    with pytest.raises(ledger.SendLedgerError, match="mutation_outcome_unknown"):
        ledger.send_once(draft, "e" * 64, lambda: result)
