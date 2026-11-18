"""Present existing Office results without performing another Office operation."""

import hmac
from typing import Annotated

from actions import ActionError
from actions.mcp import resource, tool
from pydantic import BaseModel, Field

from office_bridge.canvas_window import open_window
from office_bridge.presentation import (
    MESSAGE_UI,
    MESSAGE_URI,
    MailActions,
    Presentation,
    View,
    app_html,
    present,
)


class WindowReceipt(BaseModel):
    view_id: str
    opened: bool


class WindowView(View):
    mail: MailActions | None = None
    window_proof: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


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
    view_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
    view: WindowView,
) -> WindowReceipt:
    """Open an existing message view in an isolated browser for standalone clients or an explicit open-window request.

    Args:
        view_id: Opaque identity returned in the existing presentation. Reuse it for display retries.
        view: The same structured view. Include the presentation's mail and window_proof fields
            when supplied to enable its verified mail controls.
    """
    try:
        presentation = present(View.model_validate(view.model_dump()))
        presentation.view_id = view_id
        if isinstance(view, WindowView) and view.mail:
            presentation.mail = view.mail
            proof = presentation.proof()
            if (
                not proof
                or not view.window_proof
                or not hmac.compare_digest(view.window_proof, proof)
            ):
                raise ValueError("Mail action binding does not match this view")
            presentation.window_proof = proof
        return WindowReceipt(**open_window(presentation))
    except (OSError, ValueError, RuntimeError) as error:
        raise ActionError(
            f"Office view could not open ({type(error).__name__}). Show the existing result as text."
        ) from None
