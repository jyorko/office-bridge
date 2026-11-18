"""Present existing Office results without performing another Office operation."""

from typing import Annotated

from actions import ActionError
from actions.mcp import resource, tool
from pydantic import BaseModel, Field

from office_bridge.canvas_window import open_window
from office_bridge.presentation import (
    MESSAGE_UI,
    MESSAGE_URI,
    Presentation,
    View,
    app_html,
    present,
)


class WindowReceipt(BaseModel):
    view_id: str
    opened: bool


@resource(MESSAGE_URI, mime_type="text/html;profile=mcp-app", meta={"ui": {"csp": {}}})
def office_message_app() -> str:
    """Serve the Office message presentation app."""
    return app_html()


@tool(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
    meta=MESSAGE_UI,
)
def office_present_messages(view: View) -> Presentation:
    """Render existing Outlook messages, send receipts, or Teams messages in a local reading view.

    Args:
        view: Display fields copied from existing results. Use the actual receipt status
            for a send and include message text only when available.
    """
    return present(view)


@tool(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
    meta={"ui": {"visibility": ["model", "app"]}},
)
def office_open_message_view(
    view_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")], view: View
) -> WindowReceipt:
    """Open an existing message view in an isolated browser for standalone clients or an explicit open-window request.

    Args:
        view_id: Opaque identity returned in the existing presentation. Reuse it for display retries.
        view: The same structured view returned by the Office action. No Office operation is repeated.
    """
    try:
        presentation = present(view)
        presentation.view_id = view_id
        return WindowReceipt(**open_window(presentation))
    except (OSError, ValueError, RuntimeError) as error:
        raise ActionError(
            f"Office view could not open ({type(error).__name__}). Show the existing result as text."
        ) from None
