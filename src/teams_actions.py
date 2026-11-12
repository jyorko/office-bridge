"""Teams web actions exposed by the community Actions Runtime."""

from typing import Annotated, Literal

from actions import ActionError, Response, action
from pydantic import Field

from office_bridge.teams import backend, service
from office_bridge.teams.models import GroupReceipt, TeamsWebStatus, WebChatHistory, WebSelfReceipt


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
def teams_web_read_qa_chat(
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> Response[WebChatHistory]:
    """Read loaded messages from the verified QA-ASSIST-VERINT-DEPLOY chat.

    Args:
        limit: Maximum loaded messages to return, up to 50.
    """
    return _respond(service.read_chat, "teams_web_read_group", "QA-ASSIST-VERINT-DEPLOY", limit)


@action(is_consequential=False)
def teams_web_read_self_chat(
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> Response[WebChatHistory]:
    """Read loaded messages from the verified self chat.

    Args:
        limit: Maximum loaded messages to return, up to 50.
    """
    return _respond(service.read_chat, "teams_web_read_self", "Yorko, Joshua (You)", limit)


@action(is_consequential=True)
def teams_web_send_qa_chat(
    body: Annotated[str, Field(min_length=1, max_length=2000)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
) -> Response[GroupReceipt]:
    """Send a plain-text message to QA-ASSIST-VERINT-DEPLOY at most once per request ID.

    Args:
        body: Complete message text, sent only to the specified group chat.
        request_id: Unique request ID; uncertain outcomes cannot be retried.
    """
    return _respond(service.send_group, body, request_id)


@action(is_consequential=True)
def teams_web_send_self(
    body: Annotated[str, Field(min_length=1, max_length=2000)],
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
) -> Response[WebSelfReceipt]:
    """Send one message to the verified self chat, with at-most-once request handling.

    Args:
        body: Complete plain-text message to your own chat.
        request_id: Unique request ID; uncertain outcomes cannot be retried.
    """
    return _respond(service.send_self, body, request_id)
