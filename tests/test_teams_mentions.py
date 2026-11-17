import json

import pytest
from actions import ActionError
from actions._action import Action
from actions._customization._plugin_manager import PluginManager
from pydantic import ValidationError

import teams_actions as actions
from office_bridge import desktop
from office_bridge.teams import ledger
from office_bridge.teams.formatting import (
    BodyFormatError,
    format_body,
    validate_mention_placeholders,
)
from office_bridge.teams.models import (
    GroupReceipt,
    PersonMention,
    PersonMentions,
    WebChatRef,
)

CHAT = WebChatRef(chat_id="19:" + "a" * 32 + "@thread.v2", title="Synthetic Fixture")


def plan(
    key="person",
    label="Example Person",
    email="person@example.invalid",
):
    return PersonMentions([PersonMention(key=key, display_label=label, recipient={"email": email})])


def setup_send(tmp_path, monkeypatch, invoke):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("OFFICE_BRIDGE_ACCOUNT", "sender@example.invalid")
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "legacy-self-ledger.json")
    monkeypatch.setattr(desktop, "invoke", invoke)


def test_mention_plan_schema_is_fully_inlined_and_bounded():
    action = Action(
        PluginManager(), "teams_actions", "teams_actions.py", actions.teams_web_send_chat
    )
    schema = action.input_schema
    mentions = schema["properties"]["mentions"]
    assert mentions["type"] == "array"
    assert mentions["maxItems"] == 10
    assert "$ref" not in json.dumps(schema)
    assert "mentions" not in schema["required"]
    assert mentions["default"] == []


def test_plan_rejects_duplicate_keys_recipients_and_invalid_identity():
    item = PersonMention(key="one", display_label="One", recipient={"email": "one@example.invalid"})
    with pytest.raises(ValidationError):
        PersonMentions([item, item.model_copy(update={"display_label": "Other"})])
    with pytest.raises(ValidationError):
        PersonMentions(
            [
                item,
                item.model_copy(update={"key": "two", "display_label": "Two"}),
            ]
        )
    with pytest.raises(ValidationError):
        PersonMention(key="Person", display_label="Person", recipient={"email": "x"})


@pytest.mark.parametrize(
    ("body", "body_format"),
    [
        ("plain message", "plain"),
        ("{{mention:missing}}", "plain"),
        ("{{mention:person}} {{mention:person}}", "plain"),
        ("{{mention:unknown}}", "plain"),
        ("`{{mention:person}}`", "markdown"),
        ("*{{mention:person}}*", "markdown"),
        ("[{{mention:person}}](https://example.invalid)", "markdown"),
    ],
)
def test_reserved_placeholders_require_one_unformatted_matching_plan(body, body_format):
    with pytest.raises(BodyFormatError):
        validate_mention_placeholders(body, body_format, {"person"})


def test_placeholders_are_allowed_in_paragraphs_and_list_items():
    assert validate_mention_placeholders("Hi {{mention:person}}", "plain", {"person"}) == ["person"]
    assert validate_mention_placeholders("- Hi {{mention:person}}", "markdown", {"person"}) == [
        "person"
    ]
    prepared = format_body("- Hi {{mention:person}}", "markdown")
    assert prepared.body == "Hi {{mention:person}}"


def test_composed_text_limit_is_checked_before_capability_or_reservation(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, _arguments):
        calls.append(operation)
        pytest.fail("invalid composed body must fail before host checks")

    setup_send(tmp_path, monkeypatch, invoke)
    body = "x" * 1980 + "{{mention:person}}"
    with pytest.raises(ActionError, match="at most 2000"):
        actions.teams_web_send_chat(CHAT, body, "too-long", mentions=plan(label="a" * 30))
    assert calls == []
    assert not (tmp_path / "OfficeActionServer" / "teams-web-sends" / "group.sqlite3").exists()


def test_markdown_mentions_rejected_before_host_check_and_reservation(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, _arguments):
        calls.append(operation)
        pytest.fail("unsupported mention format must fail before host checks")

    setup_send(tmp_path, monkeypatch, invoke)
    with pytest.raises(ActionError, match="plain-text body"):
        actions.teams_web_send_chat(
            CHAT,
            "Hi {{mention:person}}",
            "markdown-mention",
            body_format="markdown",
            mentions=plan(),
        )
    assert calls == []
    database = tmp_path / "OfficeActionServer" / "teams-web-sends" / "group.sqlite3"
    assert not database.exists()


def test_mention_plan_order_must_match_body_placeholders(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, _arguments):
        calls.append(operation)
        pytest.fail("order mismatch must fail before host checks")

    setup_send(tmp_path, monkeypatch, invoke)
    two = PersonMentions(
        [
            PersonMention(
                key="first", display_label="First", recipient={"email": "first@example.invalid"}
            ),
            PersonMention(
                key="second", display_label="Second", recipient={"email": "second@example.invalid"}
            ),
        ]
    )
    with pytest.raises(ActionError, match="placeholder order"):
        actions.teams_web_send_chat(
            CHAT, "{{mention:second}} then {{mention:first}}", "order-mismatch", mentions=two
        )
    assert calls == []
    database = tmp_path / "OfficeActionServer" / "teams-web-sends" / "group.sqlite3"
    assert not database.exists()


def test_unsupported_host_refuses_before_reservation_and_native_send(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        return {
            "state": "authenticated",
            "account_verified": True,
            "host_visible": False,
            "control_visible": False,
            "foreground_interference": False,
            "restart_required": False,
            "capabilities": [],
        }

    setup_send(tmp_path, monkeypatch, invoke)
    with pytest.raises(ActionError, match="reload required"):
        actions.teams_web_send_chat(CHAT, "Hi {{mention:person}}", "unsupported", mentions=plan())
    assert [operation for operation, _ in calls] == ["teams_web_status"]
    database = tmp_path / "OfficeActionServer" / "teams-web-sends" / "group.sqlite3"
    assert not database.exists()


def test_mentions_bind_same_request_id_and_cached_receipt(tmp_path, monkeypatch):
    calls = []

    def invoke(operation, arguments):
        calls.append((operation, arguments))
        if operation == "teams_web_status":
            return {
                "state": "authenticated",
                "account_verified": True,
                "host_visible": False,
                "control_visible": False,
                "foreground_interference": False,
                "restart_required": False,
                "capabilities": ["person_mentions_v1"],
            }
        return {
            "state": "observed",
            "message_id": "message-body-1700000000000",
            "time": "2026-11-17T12:00:00Z",
            "chat_id": CHAT.chat_id,
            "title": CHAT.title,
            "mentions": [
                {
                    "key": "person",
                    "text": "Example Person",
                    "recipient_email": "person@example.invalid",
                    "identity_evidence": "candidate_email",
                    "entity_verified": True,
                }
            ],
        }

    setup_send(tmp_path, monkeypatch, invoke)
    first = actions.teams_web_send_chat(
        CHAT, "Hi {{mention:person}}", "mention-request", mentions=plan()
    )
    second = actions.teams_web_send_chat(
        CHAT, "Hi {{mention:person}}", "mention-request", mentions=plan()
    )
    assert first.result.status == "observed"
    assert second.result.status == "already_observed"
    assert second.result.mentions[0].recipient_email == "person@example.invalid"
    assert [operation for operation, _ in calls].count("teams_web_send_chat") == 1
    send_arguments = next(args for operation, args in calls if operation == "teams_web_send_chat")
    assert send_arguments["mentions"] == [
        {
            "key": "person",
            "display_label": "Example Person",
            "recipient_email": "person@example.invalid",
        }
    ]
    assert send_arguments["visible_body"] == "Hi Example Person"
    with pytest.raises(ActionError, match="conflicts"):
        actions.teams_web_send_chat(
            CHAT,
            "Hi {{mention:person}}",
            "mention-request",
            mentions=plan(label="Different Label"),
        )
    assert [operation for operation, _ in calls].count("teams_web_send_chat") == 1


def test_old_receipt_stays_unchanged_and_validates_without_mentions():
    receipt_json = (
        '{"status":"observed","chat":{"chat_id":"'
        + CHAT.chat_id
        + '","title":"Synthetic Fixture"},'
        '"message_id":"message-body-1700000000000","time":"2026-11-17T12:00:00Z"}'
    )
    receipt = GroupReceipt.model_validate_json(receipt_json)
    assert receipt.mentions == []
    assert "mentions" not in receipt.model_dump_json()
