"""No Office calls: exercise typed Outlook and Teams boundaries offline."""

import pytest
from actions import ActionError
from pydantic import ValidationError

import outlook_actions as api
from office_bridge import desktop as bridge
from office_bridge.outlook import session
from office_bridge.teams.models import ChatRef


@pytest.fixture
def fake_outlook_session(monkeypatch):
    state = {
        "result": {"items": [], "truncated": False, "scanned_count": 0},
        "calls": [],
    }

    def invoke(operation, arguments, timeout):
        state["calls"].append((operation, arguments, timeout))
        if state.get("error"):
            raise session.SessionError(state["error"])
        return state.get("result", {})

    monkeypatch.setattr(bridge.os, "name", "nt")
    monkeypatch.setattr(session, "invoke", invoke)
    return state


def test_literal_query_is_passed_as_structured_data(fake_outlook_session):
    query = "%' ; $(Remove-Item x); \" OR 1=1 — café"
    result = api.outlook_search_inbox(api.InboxQuery(subject_contains=query), limit=3)
    assert result.result.items == []
    assert fake_outlook_session["calls"] == [
        (
            "outlook_search_inbox",
            {
                "query": {
                    "subject_contains": query,
                    "sender_contains": None,
                    "received_after": None,
                    "unread_only": False,
                },
                "limit": 3,
            },
            90,
        )
    ]


@pytest.mark.parametrize("code", ["desktop_busy", "not_found", "stale_draft", "ambiguous_chat"])
def test_structured_failures_do_not_retry(fake_outlook_session, code):
    fake_outlook_session["error"] = code
    with pytest.raises(bridge.BridgeError, match=code):
        bridge.invoke("get_message", {"message": {"entry_id": "entry", "store_id": "wrong-store"}})
    assert len(fake_outlook_session["calls"]) == 1


@pytest.mark.parametrize(
    "operation",
    ["get_message"],
)
def test_read_session_timeout_is_reported_once(fake_outlook_session, operation):
    fake_outlook_session["error"] = "session_timeout"
    with pytest.raises(bridge.BridgeError, match="session_timeout"):
        bridge.invoke(operation, {})
    assert len(fake_outlook_session["calls"]) == 1


@pytest.mark.parametrize(
    "operation",
    [
        "inbox_list",
        "get_message",
        "search_inbox",
        "create_reply_draft",
        "create_mail_draft",
        "send_draft",
        "archive",
        "download_attachments",
    ],
)
def test_outlook_does_not_start_classic_powershell(operation, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Outlook must use the persistent WebView2 host.")

    monkeypatch.setattr(bridge.os, "name", "nt")
    monkeypatch.setattr(bridge, "OUTLOOK_SUPPORTED", frozenset(bridge.OUTLOOK_OPERATIONS))
    monkeypatch.setattr(bridge.subprocess, "Popen", forbidden)
    monkeypatch.setattr(
        session,
        "invoke",
        lambda name, arguments, timeout: {"items": [], "truncated": False, "scanned_count": 0},
    )
    bridge.invoke(operation, {})


@pytest.mark.parametrize(
    "operation",
    ["create_reply_draft", "create_mail_draft", "send_draft", "archive"],
)
def test_outlook_mutations_report_unknown_outcomes_without_replaying(operation, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.os, "name", "nt")
    monkeypatch.setattr(bridge, "OUTLOOK_SUPPORTED", frozenset(bridge.OUTLOOK_OPERATIONS))

    def timeout(*args, **kwargs):
        calls.append(args)
        raise session.SessionError("session_timeout")

    monkeypatch.setattr(session, "invoke", timeout)
    with pytest.raises(bridge.BridgeError, match="mutation_outcome_unknown"):
        bridge.invoke(operation, {})
    assert len(calls) == 1


def test_models_reject_executable_fields_and_bad_identity():
    with pytest.raises(ValidationError):
        api.InboxQuery.model_validate({"dasl": "anything"})
    with pytest.raises(ValidationError):
        api.MessageRef(entry_id="item", store_id="")
    with pytest.raises(ValidationError):
        ChatRef(snapshot_id="bad", exact_tree_label="chat", window_title="title")
    with pytest.raises(ValidationError):
        api.InboxQuery(subject_contains="x" * 501)


def test_output_contract_rejects_unexpected_fields(fake_outlook_session):
    fake_outlook_session["result"]["run_script"] = "danger"
    with pytest.raises((ValidationError, ActionError)):
        api.outlook_inbox_list()
