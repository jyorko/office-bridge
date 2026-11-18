"""Outlook request, message, draft, and attachment schemas."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from office_bridge.mail_body import safe_mail_html
from office_bridge.models import Identifier, Model


class MessageRef(Model):
    entry_id: Identifier | None = None
    conversation_id: Identifier | None = None
    store_id: Identifier

    @model_validator(mode="after")
    def require_identity(self):
        if self.entry_id is None and self.conversation_id is None:
            raise ValueError("A message or conversation identity is required.")
        return self


class MessageVersion(Model):
    modified_at: datetime | None = None
    version_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content_complete: bool
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
    body_html: str = ""
    body_truncated: bool
    can_create_reply_draft: bool = False

    _safe_html = field_validator("body_html")(safe_mail_html)


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
    status: Literal["submitted", "already_submitted"]
    submitted_at: datetime
    subject: Annotated[str, Field(max_length=250)] | None = None
    body_text: Annotated[str, Field(max_length=20000)] | None = None
    body_truncated: bool = False
    account_verified: bool | None = None
    sent_items_verified: bool | None = None


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
