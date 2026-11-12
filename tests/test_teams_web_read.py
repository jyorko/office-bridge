import teams_actions as actions
from office_bridge import desktop


def test_web_read_returns_bounded_loaded_history(monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "read",
            "messages": [{"id": "message-body-1", "text": "hello", "author": "A", "time": ""}],
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    result = actions.teams_web_read_self_chat(5)
    assert result.result.chat == "Yorko, Joshua (You)"
    assert result.result.messages[0].text == "hello"
    assert calls == [("teams_web_read_self", {"limit": 5})]
