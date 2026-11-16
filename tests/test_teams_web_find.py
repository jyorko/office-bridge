import pytest

import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams import lookup
from office_bridge.teams.models import WebChatRef


def test_search_returns_exact_references_from_teams_directory(monkeypatch):
    chat_id = "19:" + "a" * 32 + "@unq.gbl.spaces"
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "found",
            "scope": "teams_search",
            "items": [{"chat_id": chat_id, "title": "A Person"}],
            "truncated": False,
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    response = actions.teams_web_find_chats("A Person", 5)
    assert calls == [("teams_web_find_chats", {"query": "A Person", "limit": 5})]
    assert response.result.scope == "teams_search"
    assert response.result.items == [WebChatRef(chat_id=chat_id, title="A Person")]


@pytest.mark.parametrize(
    "result",
    [
        {"state": "listed", "scope": "teams_search", "items": [], "truncated": False},
        {"state": "found", "scope": "currently_loaded_chat_list", "items": [], "truncated": False},
        {"state": "found", "scope": "teams_search", "items": [], "truncated": 0},
        {
            "state": "found",
            "scope": "teams_search",
            "items": [{"chat_id": "bad", "title": "X"}],
            "truncated": False,
        },
    ],
)
def test_search_rejects_unverified_native_results(monkeypatch, result):
    monkeypatch.setattr(desktop, "invoke", lambda *_: result)
    with pytest.raises(lookup.LookupError):
        lookup.find_chats("person", 2)


def test_search_rejects_more_items_than_requested(monkeypatch):
    monkeypatch.setattr(
        desktop,
        "invoke",
        lambda *_: {
            "state": "found",
            "scope": "teams_search",
            "items": [
                {"chat_id": f"19:{'a' * 32}@thread.v2", "title": f"Chat {index}"}
                for index in range(2)
            ],
            "truncated": True,
        },
    )
    with pytest.raises(lookup.LookupError):
        lookup.find_chats("person", 1)
