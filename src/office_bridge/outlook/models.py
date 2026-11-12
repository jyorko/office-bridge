"""Outlook request, message, draft, and attachment schemas."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from office_bridge.models import Identifier, Model


class MessageRef(Model):
    entry_id: Identifier
    store_id: Identifier


class MessageVersion(Model):
    modified_at: datetime
    subject: str
    unread: bool
    folder_entry_id: Identifier
    flagged: bool
    importance: Literal["low", "normal", "high"]


class MailAddress(Model):
    display_name: str
    smtp_address: str | None
    recipient_type: int = 1


class MessageSummary(Model):
    ref: MessageRef
    version: MessageVersion
    sender: MailAddress
    subject: str
    received_at: datetime
    unread: bool
    flagged: bool
    importance: Literal["low", "normal", "high"]


class MessageDetail(MessageSummary):
    recipients: list[MailAddress]
    body_text: str
    body_truncated: bool


class InboxQuery(Model):
    subject_contains: Annotated[str, Field(max_length=500)] | None = None
    sender_contains: Annotated[str, Field(max_length=500)] | None = None
    received_after: datetime | None = None
    unread_only: bool = False


class InboxPage(Model):
    items: list[MessageSummary]
    truncated: bool
    scanned_count: int


class ReplyDraft(Model):
    ref: MessageRef
    source: MessageRef
    recipients: list[MailAddress]
    subject: str
    body_text: str
    sending_account: str
    digest: str


class MailDraft(Model):
    ref: MessageRef
    recipients: list[MailAddress]
    subject: str
    body_text: str
    sending_account: str
    digest: str


class SendReceipt(Model):
    draft: MessageRef
    status: Literal["submitted"]
    submitted_at: datetime


class ArchiveReceipt(Model):
    original: MessageRef
    archived: MessageRef
    archived_at: datetime


class AttachmentDownload(Model):
    index: Annotated[int, Field(ge=1, le=500)]
    filename: str
    size_bytes: Annotated[int, Field(ge=0, le=52_428_800)]
    local_path: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AttachmentDownloads(Model):
    ref: MessageRef
    items: Annotated[list[AttachmentDownload], Field(max_length=25)]
