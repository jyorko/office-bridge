"""Teams web actions exposed by the community Actions Runtime."""

from typing import Annotated, Literal

from actions import ActionError, Response, action
from pydantic import Field, RootModel

from office_bridge.teams import backend, lookup, messages, service, work_tracking
from office_bridge.teams.messages import (
    MentionEvidenceReport,
    MessageLink,
    MessageReactions,
    ReactionReceipt,
)
from office_bridge.teams.models import (
    EditReceipt,
    GroupReceipt,
    PersonMentions,
    TeamsWebStatus,
    UploadReadiness,
    UploadReceipt,
    UploadStatus,
    WebChatHistory,
    WebChatRef,
    WebChatSearchResult,
)
from office_bridge.teams.work_tracking import Outcome, TrackingPolicy, WorkStatus


class TrackingChats(RootModel[list[WebChatRef]]):
    """Keep nested model definitions attached to the array's schema."""

    root: Annotated[list[WebChatRef], Field(max_length=50)]


def _respond(operation, *args):
    try:
        return Response(result=operation(*args))
    except service.TeamsError as error:
        raise ActionError(str(error)) from None


@action(is_consequential=False)
def teams_web_status(
    browser_engine: Literal["chromium", "msedge", "webview2"] = "webview2",
) -> Response[TeamsWebStatus]:
    """Check Teams authentication in an isolated background browser profile.

    Args:
        browser_engine: Background WebView2 with Windows SSO, or a diagnostic browser.
    """
    result = backend.backend_status(browser_engine)
    result.setdefault("browser_engine", browser_engine)
    return Response(result=TeamsWebStatus.model_validate(result))


@action(is_consequential=False)
def teams_web_list_chats(
    name: Annotated[str | None, Field(min_length=1, max_length=120)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 100,
) -> Response[WebChatSearchResult]:
    """List verified references in the currently loaded Teams chat list.

    Args:
        name: Optional exact chat title filter.
        limit: Maximum references to return, up to 100.
    """
    return _respond(service.list_chats, name, limit)


@action(is_consequential=False)
def teams_web_find_chats(
    query: Annotated[str, Field(min_length=1, max_length=120)],
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> Response[WebChatSearchResult]:
    """Search the Teams directory and return verified exact chat references.

    Args:
        query: Bounded text to search in the Teams directory.
        limit: Maximum references to return, up to 50.
    """
    try:
        return Response(result=lookup.find_chats(query, limit))
    except lookup.LookupError as error:
        raise ActionError(str(error)) from None


@action(is_consequential=False)
def teams_web_read_chat(
    chat: WebChatRef,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> Response[WebChatHistory]:
    """Read a bounded chat history, loading older messages up to the requested limit.

    Args:
        chat: Exact chat reference returned by teams_web_find_chats or teams_web_list_chats.
        limit: Maximum messages to collect, up to 50; fewer may return at the time bound.
    """
    return _respond(service.read_chat, chat, limit)


@action(is_consequential=False)
def teams_web_inspect_mention_evidence(
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
) -> Response[MentionEvidenceReport]:
    """Read sanitized DOM structure for a settled mention in one exact existing message.

    This diagnostic uses the hidden auxiliary WebView2 and leaves the primary composer unchanged.
    It returns element structure and hashed value shapes only; it never returns message text,
    display names, email addresses, account IDs, or raw identity attributes.

    Args:
        chat: Exact 19: chat reference returned by Teams chat listing or search.
        message_id: Exact settled message ID from a fresh read of that chat.
    """
    return _respond(messages.inspect_mention_evidence, chat, message_id)


@action(is_consequential=True)
def teams_web_send_chat(
    chat: WebChatRef,
    body: Annotated[str, Field(min_length=1, max_length=2000)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    body_format: Literal["plain", "markdown"] = "plain",
    mentions: PersonMentions = PersonMentions([]),
) -> Response[GroupReceipt]:
    """Send one message to the verified exact chat reference at most once per request ID.

    Args:
        chat: Exact chat reference returned by teams_web_find_chats or teams_web_list_chats.
        body: Complete message text, sent only to the referenced chat.
        request_id: Unique request ID; uncertain outcomes cannot be retried.
        body_format: Plain text or supported CommonMark formatting.
        mentions: Optional verified person mentions keyed by placeholders in the body. The Teams
            host must report person_mentions_v1 before any send reservation is created. Notification
            delivery is not verified.
    """
    return _respond(service.send_chat, chat, body, request_id, body_format, mentions)


@action(is_consequential=True)
def teams_web_edit_message(
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
    expected_text: Annotated[str, Field(max_length=2000)],
    body: Annotated[str, Field(min_length=1, max_length=2000)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    body_format: Literal["plain", "markdown"] = "plain",
) -> Response[EditReceipt]:
    """Edit an existing own message after exact chat, owner and text verification.

    Args:
        chat: Exact chat reference returned by teams_web_find_chats or teams_web_list_chats.
        message_id: Exact existing message ID in the currently loaded chat.
        expected_text: Exact old message text used for optimistic concurrency checks.
        body: Replacement message text.
        request_id: Unique edit ID; uncertain outcomes cannot be retried.
        body_format: Plain text or supported CommonMark formatting.
    """
    return _respond(
        service.edit_chat,
        chat,
        message_id,
        expected_text,
        body,
        request_id,
        body_format,
    )


@action(is_consequential=False)
def teams_web_upload_readiness(chat: WebChatRef) -> Response[UploadReadiness]:
    """Inspect upload support for one exact chat without changing its composer.

    Args:
        chat: Exact chat reference returned by teams_web_find_chats or teams_web_list_chats.
    """
    return _respond(service.upload_readiness, chat)


@action(is_consequential=True)
def teams_web_upload_files(
    chat: WebChatRef,
    file_paths: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=32767)]],
        Field(min_length=1, max_length=10),
    ],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
) -> Response[UploadReceipt]:
    """Upload 1 to 10 bounded local files to one verified Teams chat at most once.

    Files must be absolute local regular files with unique safe names, no larger
    than 25 MiB each or 50 MiB total. Existing composer drafts or attachments
    block the upload. An uncertain result is never replayed.

    Args:
        chat: Exact chat reference returned by teams_web_find_chats or teams_web_list_chats.
        file_paths: Absolute local file paths; paths are not retained in the ledger.
        request_id: Unique upload ID; uncertain outcomes require status reconciliation.
    """
    return _respond(service.upload_files, chat, file_paths, request_id)


@action(is_consequential=False)
def teams_web_upload_status(
    chat: WebChatRef,
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
) -> Response[UploadStatus]:
    """Reconcile a prior upload receipt without clicking or retrying it.

    If no durable settled receipt exists, inspect the exact chat manually before
    considering any next step. The action never submits files.

    Args:
        chat: The same exact chat reference originally used for the upload.
        request_id: Exact upload ID to reconcile.
    """
    return _respond(service.upload_status, chat, request_id)


@action(is_consequential=False)
def teams_web_message_link(
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
) -> Response[MessageLink]:
    """Return a deep link to an exact existing message without using the clipboard.

    Args:
        chat: Exact chat reference returned by list or find.
        message_id: Message ID returned by read_chat.
    """
    return _respond(messages.link, chat, message_id)


@action(is_consequential=False)
def teams_web_message_reactions(
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
    expected_text: Annotated[str, Field(max_length=2000)],
) -> Response[MessageReactions]:
    """List supported reaction controls for one exact message without reacting.

    Args:
        chat: Exact chat reference returned by list or find.
        message_id: Message ID returned by read_chat.
        expected_text: Exact message text returned by read_chat.
    """
    return _respond(messages.reactions, chat, message_id, expected_text)


@action(is_consequential=True)
def teams_web_react_message(
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
    expected_text: Annotated[str, Field(max_length=2000)],
    reaction: Annotated[str, Field(min_length=1, max_length=80)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
) -> Response[ReactionReceipt]:
    """Add one authorized reaction; an existing own reaction is never toggled off.

    Args:
        chat: Exact chat reference returned by list or find.
        message_id: Message ID returned by read_chat.
        expected_text: Exact message text used to guard against changes.
        reaction: Exact enabled name returned by message_reactions.
        request_id: Unique reaction ID; uncertain outcomes cannot be replayed.
    """
    return _respond(messages.react, chat, message_id, expected_text, reaction, request_id)


@action(is_consequential=True)
def teams_work_tracking_configure(
    session_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    chats: TrackingChats,
    enabled: bool,
) -> Response[TrackingPolicy]:
    """Opt in to Eyes tracking for an explicitly authorized session and exact chats.

    Eyes is reserved for automation on enrolled task messages. Pre-existing Eyes
    is preserved. Disabling prevents new claims; authorized cleanup remains allowed.
    Changing chat scope requires a new session ID. This does not authorize replies.

    Args:
        session_id: Stable ID for the user-authorized tracking scope.
        chats: Exact chat references enrolled by the user.
        enabled: Enable new claims, or disable them while permitting cleanup.
    """
    return _respond(work_tracking.configure, session_id, chats.root, enabled)


@action(is_consequential=True)
def teams_work_begin(
    session_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    work_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
    expected_content: Annotated[str, Field(max_length=2000)],
) -> Response[WorkStatus]:
    """Claim an assigned task message and add Eyes only within an opted-in scope.

    Use content_text from message_reactions, excluding reaction summaries.
    Mark meaningful active investigation, not passive reading or queued work.
    Reuse work_id for the same claim; completed claims cannot be reactivated.
    Status failures do not prevent the authorized underlying work.

    Args:
        session_id: Previously authorized tracking session.
        work_id: Stable unique ID for this work claim.
        chat: Exact enrolled chat reference.
        message_id: Exact task message ID returned by read_chat.
        expected_content: Current content_text returned by message_reactions.
    """
    return _respond(work_tracking.begin, session_id, work_id, chat, message_id, expected_content)


@action(is_consequential=True)
def teams_work_finish(
    session_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    work_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
    outcome: Outcome,
) -> Response[WorkStatus]:
    """Release a claim when work completes, pauses, fails, or is cancelled.

    Only the last active claim may remove Eyes, and only if automation owns it.
    Interim replies do not finish ongoing investigation. Completed means the
    requested outcome was verified. No completion reaction or reply is sent.
    Pending/uncertain transitions block automatic retries across new request IDs.

    Args:
        session_id: Tracking session that owns the claim.
        work_id: Exact claim ID used by work_begin.
        chat: Exact enrolled chat reference.
        message_id: Exact task message ID used by work_begin.
        outcome: Verified completion, pause, cancellation, or failure.
    """
    return _respond(work_tracking.finish, session_id, work_id, chat, message_id, outcome)


@action(is_consequential=False)
def teams_work_status(
    chat: WebChatRef,
    message_id: Annotated[str, Field(pattern=r"^message-body-[0-9]{13}$")],
) -> Response[WorkStatus]:
    """Reconcile durable claims after worker replacement or a crash without clicking.

    Inspect active claims and ownership before resuming. Finish known abandoned
    claims explicitly; a stale claim never implies success or expires automatically.

    Args:
        chat: Exact tracked chat reference.
        message_id: Exact task message ID used by work_begin.
    """
    return _respond(work_tracking.status, chat, message_id)
