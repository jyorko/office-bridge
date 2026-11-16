"""Teams web actions exposed by the community Actions Runtime."""

from typing import Annotated, Literal

from actions import ActionError, Response, action
from pydantic import Field

from office_bridge.teams import backend, lookup, service
from office_bridge.teams.models import (
    EditReceipt,
    GroupReceipt,
    TeamsWebStatus,
    WebChatHistory,
    WebChatRef,
    WebChatSearchResult,
)


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


@action(is_consequential=True)
def teams_web_send_chat(
    chat: WebChatRef,
    body: Annotated[str, Field(min_length=1, max_length=2000)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    body_format: Literal["plain", "markdown"] = "plain",
) -> Response[GroupReceipt]:
    """Send one message to the verified exact chat reference at most once per request ID.

    Args:
        chat: Exact chat reference returned by teams_web_find_chats or teams_web_list_chats.
        body: Complete message text, sent only to the referenced chat.
        request_id: Unique request ID; uncertain outcomes cannot be retried.
        body_format: Plain text or supported CommonMark formatting.
    """
    return _respond(service.send_chat, chat, body, request_id, body_format)


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
