import pytest
from actions import ActionError

import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams.models import WebChatRef

CHAT = WebChatRef(chat_id="19:" + "a" * 32 + "@thread.v2", title="Synthetic Fixture")
MESSAGE_ID = "message-body-1700000000000"


def test_mention_inspector_returns_only_validated_sanitized_evidence(monkeypatch):
    private_values = {
        "Private Display Name",
        "private@example.invalid",
        "private-recipient-id",
        MESSAGE_ID,
    }
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "inspected",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": MESSAGE_ID,
            "capture_date": "2026-11-17",
            "author": "Private Display Name",
            "text": "Private Display Name",
            "mentions": [
                {
                    "tag": "mention",
                    "depth": 0,
                    "ancestors": ["readonly"],
                    "attribute_names": ["mri", "data-email", "aria-label"],
                    "attributes": [
                        {
                            "name": "mri",
                            "value_shape": {
                                "prefix_class": "identifier",
                                "length": 20,
                                "hash": "0123abcd",
                            },
                        },
                        {
                            "name": "data-email",
                            "value_shape": {
                                "prefix_class": "email",
                                "length": 21,
                                "hash": "1234abcd",
                            },
                        },
                        {
                            "name": "aria-label",
                            "value_shape": {
                                "prefix_class": "text",
                                "length": 20,
                                "hash": "2345abcd",
                            },
                        },
                    ],
                    "role": "",
                    "role_shape": {"prefix_class": "empty", "length": 0, "hash": "811c9dc5"},
                    "itemtype": "",
                    "data_tid_shape": {"prefix_class": "empty", "length": 0, "hash": "811c9dc5"},
                }
            ],
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    result = actions.teams_web_inspect_mention_evidence(CHAT, MESSAGE_ID)
    serialized = result.result.model_dump_json()
    assert [operation for operation, _ in calls] == ["teams_web_inspect_mention_evidence"]
    assert calls[0][1] == {
        "chat": CHAT.model_dump(mode="json"),
        "message_id": MESSAGE_ID,
    }
    assert result.result.state == "inspected"
    assert result.result.mentions[0].tag == "mention"
    assert result.result.mentions[0].attributes[0].name == "mri"
    for private_value in private_values:
        assert private_value not in serialized


def test_mention_inspector_rejects_unbounded_or_raw_identity_evidence(monkeypatch):
    def invoke(_operation, _arguments):
        return {
            "state": "inspected",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "message_id": MESSAGE_ID,
            "capture_date": "2026-11-17",
            "mentions": [
                {
                    "tag": "mention",
                    "depth": 0,
                    "ancestors": [],
                    "attribute_names": ["email"],
                    "attributes": [{"name": "email", "value": "private@example.invalid"}],
                    "role": "",
                    "itemtype": "",
                    "data_tid_shape": {"prefix_class": "empty", "length": 0, "hash": "811c9dc5"},
                }
            ],
        }

    monkeypatch.setattr(desktop, "invoke", invoke)
    with pytest.raises(ActionError, match="safely sanitized"):
        actions.teams_web_inspect_mention_evidence(CHAT, MESSAGE_ID)
