import pytest

from office_bridge import desktop
from office_bridge.teams import messages
from office_bridge.teams.models import WebChatRef
from office_bridge.teams.service import TeamsError

CHAT = WebChatRef(chat_id="19:" + "a" * 32 + "@thread.v2", title="Example")
MESSAGE = "message-body-1700000000000"


def test_reaction_is_reserved_once_and_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = []

    def invoke(operation, arguments):
        calls.append(operation)
        return {
            "state": "reacted",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": MESSAGE,
            "reaction": "Like",
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    assert messages.react(CHAT, MESSAGE, "hello", "Like", "one").status == "reacted"
    assert messages.react(CHAT, MESSAGE, "hello", "Like", "one").status == "reacted"
    assert len(calls) == 1
    with pytest.raises(TeamsError, match="conflicts"):
        messages.react(CHAT, MESSAGE, "hello", "Heart", "one")
    assert len(calls) == 1


def test_uncertain_reaction_cannot_be_replayed(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = []

    def fail(*args):
        calls.append(args)
        raise desktop.BridgeError("mutation_outcome_unknown")

    monkeypatch.setattr(desktop, "invoke", fail)
    with pytest.raises(TeamsError):
        messages.react(CHAT, MESSAGE, "hello", "Like", "two")
    with pytest.raises(TeamsError, match="uncertain"):
        messages.react(CHAT, MESSAGE, "hello", "Like", "two")
    assert len(calls) == 1


def test_link_rejects_wrong_message_identity(monkeypatch):
    monkeypatch.setattr(desktop, "invoke", lambda *_: {"state": "linked"})
    with pytest.raises(TeamsError, match="identity"):
        messages.link(CHAT, MESSAGE)
