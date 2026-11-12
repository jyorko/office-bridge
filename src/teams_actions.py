"""Teams web actions exposed by the community Actions Runtime."""

from typing import Annotated, Literal

from actions import ActionError, Response, action
from pydantic import Field

from office_bridge.teams import backend, service
from office_bridge.teams.models import (
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
def teams_web_read_chat(
    chat: WebChatRef,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> Response[WebChatHistory]:
    """Read loaded history from the verified exact chat reference.

    Args:
        chat: Exact chat reference returned by teams_web_list_chats.
        limit: Maximum loaded messages to return, up to 50.
    """
    return _respond(service.read_chat, chat, limit)


@action(is_consequential=True)
def teams_web_send_chat(
    chat: WebChatRef,
    body: Annotated[str, Field(min_length=1, max_length=2000)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
) -> Response[GroupReceipt]:
    """Send one message to the verified exact chat reference at most once per request ID.

    Args:
        chat: Exact chat reference returned by teams_web_list_chats.
        body: Complete plain-text message, sent only to the referenced chat.
        request_id: Unique request ID; uncertain outcomes cannot be retried.
    """
    return _respond(service.send_chat, chat, body, request_id)
