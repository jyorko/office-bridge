"""Background-safe Windows UI Automation actions for desktop Teams."""

from actions import Response
from actions.mcp import tool

from office_bridge import desktop
from office_bridge.models import Limit
from office_bridge.presentation import MESSAGE_UI, MessageResponse, with_presentation
from office_bridge.teams.models import ChatHistory, ChatPage, ChatRef


@tool(read_only_hint=True, open_world_hint=False)
def teams_list_chats(limit: Limit = 30) -> Response[ChatPage]:
    """List recognized loaded chats from the unique Teams Chat main window.

    Args:
        limit: Maximum recognized chats, from 1 to 50.
    """
    return desktop.run("list_chats", ChatPage, limit=limit)


@tool(read_only_hint=True, open_world_hint=False, meta=MESSAGE_UI)
def teams_read_chat(chat: ChatRef, limit: Limit = 50) -> MessageResponse[ChatHistory]:
    """Select an observed chat and read its loaded history. Changes visible chat selection.

    Args:
        chat: Exact chat reference returned by the current chat listing.
        limit: Maximum loaded messages, from 1 to 50.
    """
    return with_presentation(desktop.run("read_chat", ChatHistory, chat=chat, limit=limit))
