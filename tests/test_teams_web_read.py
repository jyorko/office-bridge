import desktop_bridge
import teams_web_actions as actions


def test_web_read_returns_bounded_loaded_history(monkeypatch):
    calls = []
    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {"state": "read", "messages": [{"id": "message-body-1", "text": "hello", "author": "A", "time": ""}]}
    monkeypatch.setattr(desktop_bridge, "invoke", invoke)
    result = actions.teams_web_read_self_chat(5)
    assert result.result.chat == "Yorko, Joshua (You)"
    assert result.result.messages[0].text == "hello"
    assert calls == [("teams_web_read_self", {"limit": 5})]
