"""Classic Outlook actions exposed by the community Actions Runtime."""

from typing import Annotated

from actions import ActionError, Response
from actions.mcp import tool
from pydantic import Field

from office_bridge import desktop
from office_bridge.models import Identifier, Limit, Text
from office_bridge.outlook.models import (
    ArchiveReceipt,
    AttachmentDownloads,
    InboxPage,
    InboxQuery,
    MailDraft,
    MessageDetail,
    MessageRef,
    MessageVersion,
    ReplyDraft,
    SendReceipt,
)
from office_bridge.outlook.service import send_draft as submit_outlook_draft


@tool(read_only_hint=True, open_world_hint=False)
def outlook_inbox_list(limit: Limit = 20, unread_only: bool = False) -> Response[InboxPage]:
    """List recent inbox conversations within a bounded scan of 500 messages.

    The returned reference is a conversation identity. Individual reads fail
    closed until the host can verify an exact message identity.

    Args:
        limit: Maximum results, from 1 to 50.
        unread_only: Return only unread mail when true.
    """
    return desktop.run("inbox_list", InboxPage, limit=limit, unread_only=unread_only)


@tool(read_only_hint=True, open_world_hint=False)
def outlook_get_message(message: MessageRef) -> Response[MessageDetail]:
    """Read one exact message with its body capped at 20000 characters.

    This operation fails closed when Outlook does not expose an exact message
    identity. Draft, reply, attachment, send, and archive actions are unavailable.

    Args:
        message: A conversation reference from a list, or an exact message
            reference returned by an earlier read.
    """
    return desktop.run("get_message", MessageDetail, message=message)


@tool(read_only_hint=True, open_world_hint=False)
def outlook_search_inbox(query: InboxQuery, limit: Limit = 20) -> Response[InboxPage]:
    """Search literal substrings within the newest 500 inbox items.

    Args:
        query: Literal subject, sender, date, and unread filters.
        limit: Maximum results, from 1 to 50.
    """
    return desktop.run("search_inbox", InboxPage, query=query, limit=limit)


@tool(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
def outlook_create_reply_draft(
    source: MessageRef,
    body_text: Text,
    expected_source: MessageVersion,
    reply_all: bool = False,
) -> Response[ReplyDraft]:
    """Save an attachment-free, plain-text reply or reply-all draft. Never sends.

    Args:
        source: Exact message reference returned by outlook_get_message.
        body_text: Complete plain-text reply body, at most 20000 characters.
        expected_source: Content/version digest returned by the last message read.
        reply_all: Include the source message's other recipients when true.
    """
    return desktop.run(
        "create_reply_draft",
        ReplyDraft,
        source=source,
        body_text=body_text,
        expected_source=expected_source,
        reply_all=reply_all,
    )


@tool(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
def outlook_send_draft(
    draft: MessageRef, expected_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
) -> Response[SendReceipt]:
    """Submit an inspected plain-text draft. Requires explicit user authorization to send.

    Args:
        draft: Entry and store identity returned when creating the draft.
        expected_digest: Returned digest of body, subject, recipients, and sending account.
    """
    try:
        receipt = submit_outlook_draft(draft, expected_digest)
        return Response(result=receipt)
    except desktop.BridgeError as error:
        raise ActionError(f"Outlook send failed: {error.code}.") from None


@tool(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
def outlook_create_mail_draft(
    recipient_email: Annotated[str, Field(min_length=3, max_length=254)],
    subject: Annotated[str, Field(min_length=1, max_length=250)],
    body_text: Text,
) -> Response[MailDraft]:
    """Save a plain-text test email addressed only to the signed-in company user. Never sends.

    Args:
        recipient_email: The signed-in user's company email; other recipients are rejected.
        subject: Plain-text subject, from 1 to 250 characters.
        body_text: Complete plain-text message body, at most 20000 characters.
    """
    return desktop.run(
        "create_mail_draft",
        MailDraft,
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
    )


@tool(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
def outlook_archive(
    message: MessageRef, expected_source: MessageVersion
) -> Response[ArchiveReceipt]:
    """Move unchanged inbox mail to its existing Archive folder and return its new identity.

    Args:
        message: Exact message reference returned by outlook_get_message.
        expected_source: Unchanged content/version digest returned by the last read.
    """
    return desktop.run("archive", ArchiveReceipt, message=message, expected_source=expected_source)


@tool(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
def outlook_download_attachments(
    entry_id: Identifier,
    store_id: Identifier,
    pdf_only: bool = False,
    conversation_id: Identifier | None = None,
) -> Response[AttachmentDownloads]:
    """Save mail attachments locally without opening them, up to 25 files and 50 MiB.

    Args:
        entry_id: Exact message identity returned by outlook_get_message.
        store_id: Exact store identity returned with the message.
        pdf_only: Save only PDF attachments, excluding signature images and other files.
        conversation_id: Conversation identity returned with the message, when available.
    """
    reference = MessageRef(
        entry_id=entry_id, store_id=store_id, conversation_id=conversation_id
    )
    try:
        result = desktop.invoke(
            "download_attachments",
            {**reference.model_dump(mode="json", exclude_none=True), "pdf_only": pdf_only},
        )
        return Response(result=AttachmentDownloads.model_validate(result))
    except desktop.BridgeError as error:
        raise ActionError(f"Attachment download failed: {error.code}.") from None
