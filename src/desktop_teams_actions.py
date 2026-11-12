"""Background-safe Windows UI Automation actions for desktop Teams."""

from typing import Annotated

from actions import Response
from actions.mcp import tool
from pydantic import Field

from office_bridge import desktop
from office_bridge.models import Limit
from office_bridge.teams.models import ChatHistory, ChatPage, ChatRef, SelfMessageReceipt


@tool(read_only_hint=True, open_world_hint=False)
def teams_list_chats(limit: Limit = 30) -> Response[ChatPage]:
    """List recognized loaded chats from the unique Teams Chat main window.

    Args:
        limit: Maximum recognized chats, from 1 to 50.
    """
    return desktop.run("list_chats", ChatPage, limit=limit)


@tool(read_only_hint=True, open_world_hint=False)
def teams_read_chat(chat: ChatRef, limit: Limit = 50) -> Response[ChatHistory]:
    """Select an observed chat and read its loaded history. Changes visible chat selection.

    Args:
        chat: Exact chat reference returned by the current chat listing.
        limit: Maximum loaded messages, from 1 to 50.
    """
    return desktop.run("read_chat", ChatHistory, chat=chat, limit=limit)


@tool(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
def teams_send_self_message(
    body_text: Annotated[str, Field(min_length=1, max_length=2000)],
) -> Response[SelfMessageReceipt]:
    """Send one message only to the signed-in company user's self chat, then observe it. Requires explicit user authorization. Never automatically retry an uncertain result.

    Args:
        body_text: Plain-text self-chat message, from 1 to 2000 characters.
    """
    return desktop.run("send_self_message", SelfMessageReceipt, body_text=body_text)
