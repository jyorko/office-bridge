"""Teams directory search through the persistent, isolated Teams host."""

from office_bridge import desktop
from office_bridge.teams.models import WebChatRef, WebChatSearchResult


class LookupError(RuntimeError):
    """A Teams search could not be verified safely."""


def find_chats(query: str, limit: int) -> WebChatSearchResult:
    try:
        result = desktop.invoke("teams_web_find_chats", {"query": query, "limit": limit})
        if (
            result.get("state") != "found"
            or result.get("scope") != "teams_search"
            or type(result.get("truncated")) is not bool
            or not isinstance(result.get("items"), list)
            or len(result["items"]) > limit
        ):
            raise ValueError("invalid search result")
        items = [WebChatRef.model_validate(item) for item in result["items"]]
        if len({item.chat_id for item in items}) != len(items):
            raise ValueError("duplicate chat identity")
        return WebChatSearchResult(items=items, scope="teams_search", truncated=result["truncated"])
    except desktop.BridgeError as error:
        raise LookupError(f"Could not safely search Teams chats: {error.code}.") from None
    except (KeyError, TypeError, ValueError):
        raise LookupError("Could not safely verify Teams search results.") from None
