"""Outlook Web references and receipt states remain explicit at the API boundary."""

import pytest
from pydantic import ValidationError

from office_bridge.outlook.models import MessageRef, MessageVersion, SendReceipt


def test_conversation_reference_is_distinct_from_exact_message_identity():
    conversation = MessageRef(
        conversation_id="conversation-1",
        store_id="outlook-web-account-hash",
    )
    exact_message = MessageRef(
        entry_id="message-1",
        conversation_id="conversation-1",
        store_id="outlook-web-account-hash",
    )
    assert conversation.entry_id is None
    assert conversation.conversation_id == "conversation-1"
    assert exact_message.entry_id == "message-1"


def test_message_reference_requires_at_least_one_identity():
    with pytest.raises(ValidationError):
        MessageRef(store_id="outlook-web-account-hash")


def test_web_version_uses_digest_without_fabricated_modified_time():
    version = MessageVersion(
        modified_at=None,
        version_digest="a" * 64,
        content_complete=False,
        subject="Subject",
        unread=False,
        folder_entry_id="inbox",
        flagged=False,
        importance="normal",
    )
    assert version.modified_at is None
    assert version.version_digest == "a" * 64

    with pytest.raises(ValidationError):
        MessageVersion(
            modified_at=None,
            version_digest="not-a-digest",
            content_complete=False,
            subject="Subject",
            unread=False,
            folder_entry_id="inbox",
            flagged=False,
            importance="normal",
        )


def test_send_receipt_accepts_cached_state_and_verified_plain_preview():
    receipt = SendReceipt(
        draft=MessageRef(entry_id="draft-1", store_id="outlook-web-account-hash"),
        status="already_submitted",
        submitted_at="2026-11-18T15:00:00Z",
        subject="Subject",
        body_text="Plain preview",
        body_truncated=False,
        account_verified=True,
        sent_items_verified=True,
    )
    assert receipt.status == "already_submitted"
    assert receipt.sent_items_verified is True
    with pytest.raises(ValidationError):
        SendReceipt(
            draft=receipt.draft,
            status="retry",
            submitted_at=receipt.submitted_at,
            subject=receipt.subject,
            body_text=receipt.body_text,
            body_truncated=receipt.body_truncated,
            account_verified=True,
            sent_items_verified=True,
        )


def test_legacy_send_receipt_needs_no_invented_preview_or_proof():
    receipt = SendReceipt(
        draft=MessageRef(entry_id="draft-legacy", store_id="store"),
        status="submitted",
        submitted_at="2026-11-18T15:00:00Z",
    )
    assert receipt.subject is None
    assert receipt.body_text is None
    assert receipt.sent_items_verified is None
