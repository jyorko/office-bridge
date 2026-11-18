"""Present existing Office results without performing another Office operation."""

import sqlite3
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
from office_bridge.window_view_cache import resolve, retain


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
    display = present(view)
    try:
        retain(display)
    except (OSError, ValueError, sqlite3.Error):
        pass
    return display


@tool(
    title="Open Office reading window",
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
    meta={"ui": {"visibility": ["model", "app"]}},
)
def office_open_message_view(
    window: Annotated[str, Field(min_length=1, max_length=160)],
    view_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
) -> WindowReceipt:
    """Open an existing message view in an isolated browser for standalone clients or an explicit open-window request.

    Args:
        window: Readable window label returned in the existing presentation.
        view_id: Opaque identity returned in that presentation. Reuse both fields for display retries.
    """
    try:
        presentation = resolve(window, view_id)
        return WindowReceipt(**open_window(presentation))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        raise ActionError(
            f"Office view could not open ({type(error).__name__}). Show the existing result as text."
        ) from None
