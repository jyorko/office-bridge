import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams.models import WebChatRef

CHAT_ID = "19:" + "a" * 32 + "@thread.v2"


def test_list_returns_generic_exact_chat_references(monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "listed",
            "items": [{"chat_id": CHAT_ID, "title": "Example Group"}],
            "truncated": False,
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    result = actions.teams_web_list_chats("Example Group", 10)
    assert result.result.items == [WebChatRef(chat_id=CHAT_ID, title="Example Group")]
    assert calls == [("teams_web_list_chats", {"name": "Example Group", "limit": 10})]


def test_generic_read_rechecks_the_supplied_reference(monkeypatch):
    calls = []
    chat = WebChatRef(chat_id=CHAT_ID, title="Example Group")

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        assert arguments == {"chat": chat.model_dump(mode="json"), "limit": 5}
        return {
            "state": "read",
            "chat_id": CHAT_ID,
            "title": "Example Group",
            "messages": [
                {"id": "message-body-1", "text": "hello", "author": "Example User", "time": ""}
            ],
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    result = actions.teams_web_read_chat(chat, 5)
    assert result.result.chat == chat
    assert result.result.messages[0].text == "hello"
    assert calls == [("teams_web_read_chat", {"chat": chat.model_dump(mode="json"), "limit": 5})]


def test_self_chat_is_discovered_as_a_generic_reference(monkeypatch):
    chat = WebChatRef(chat_id="48:notes", title="Example User")
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        if operation == "teams_web_list_chats":
            return {"state": "listed", "items": [chat.model_dump()], "truncated": False}
        return {
            "state": "read",
            "chat_id": chat.chat_id,
            "title": chat.title,
            "messages": [],
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    listed = actions.teams_web_list_chats()
    result = actions.teams_web_read_chat(listed.result.items[0], 10)
    assert result.result.chat == chat
    assert [call[0] for call in calls] == ["teams_web_list_chats", "teams_web_read_chat"]
