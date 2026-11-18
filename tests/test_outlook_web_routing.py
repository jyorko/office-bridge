"""Outlook actions use the launcher-owned WebView2 controller boundary."""

import pytest

from office_bridge import desktop
from office_bridge.outlook import session


@pytest.mark.parametrize(
    ("operation", "web_operation", "arguments"),
    [
        ("inbox_list", "outlook_inbox_list", {"limit": 20, "unread_only": False}),
        (
            "get_message",
            "outlook_get_message",
            {"message": {"entry_id": "entry", "store_id": "store"}},
        ),
        (
            "search_inbox",
            "outlook_search_inbox",
            {"query": {"subject_contains": "invoice"}, "limit": 10},
        ),
        ("create_reply_draft", "outlook_create_reply_draft", {"body_text": "reply"}),
        ("create_mail_draft", "outlook_create_mail_draft", {"subject": "draft"}),
        ("send_draft", "outlook_send_draft", {"expected_digest": "a" * 64}),
        ("archive", "outlook_archive", {"expected_source": {}}),
        ("download_attachments", "outlook_download_attachments", {"pdf_only": True}),
    ],
)
def test_outlook_operations_use_persistent_webview_session(
    monkeypatch, operation, web_operation, arguments
):
    calls = []

    def invoke(name, payload, timeout):
        calls.append((name, payload, timeout))
        return {"accepted": True}

    monkeypatch.setattr(desktop.os, "name", "nt")
    monkeypatch.setattr(desktop, "OUTLOOK_SUPPORTED", frozenset(desktop.OUTLOOK_OPERATIONS))
    monkeypatch.setattr(session, "invoke", invoke)
    assert desktop.invoke(operation, arguments) == {"accepted": True}
    assert len(calls) == 1
    assert calls[0][:2] == (web_operation, arguments)
    assert calls[0][2] == (300 if operation == "download_attachments" else 90)


@pytest.mark.parametrize(
    "operation",
    [
        "create_reply_draft",
        "create_mail_draft",
        "send_draft",
        "archive",
    ],
)
def test_uncertain_outlook_mutations_are_never_retried(monkeypatch, operation):
    calls = []

    def invoke(*args, **kwargs):
        calls.append((args, kwargs))
        raise session.SessionError("session_timeout")

    monkeypatch.setattr(desktop.os, "name", "nt")
    monkeypatch.setattr(desktop, "OUTLOOK_SUPPORTED", frozenset(desktop.OUTLOOK_OPERATIONS))
    monkeypatch.setattr(session, "invoke", invoke)
    with pytest.raises(desktop.BridgeError, match="mutation_outcome_unknown"):
        desktop.invoke(operation, {})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "operation",
    ["create_reply_draft", "create_mail_draft", "send_draft", "archive", "download_attachments"],
)
def test_unaccepted_mailbox_writes_stay_disabled(monkeypatch, operation):
    monkeypatch.setattr(desktop.os, "name", "nt")

    def forbidden(*args, **kwargs):
        raise AssertionError("Unaccepted mailbox writes must not reach the host")

    monkeypatch.setattr(session, "invoke", forbidden)
    with pytest.raises(desktop.BridgeError, match="outlook_operation_unavailable"):
        desktop.invoke(operation, {})


def test_launcher_keeps_outlook_window_hidden_by_default():
    root = desktop.NATIVE.parents[2]
    bootstrap = (desktop.NATIVE / "outlook" / "bootstrap.ps1").read_text(encoding="utf-8")
    supervisor = (root / "src/office_bridge/runtime/outlook_supervisor.py").read_text(
        encoding="utf-8"
    )
    assert "[switch]$Visible" in bootstrap
    assert "$Visible.IsPresent" in bootstrap
    assert '"-Visible"' not in supervisor


def test_outlook_webview2_audio_is_enabled_before_navigation():
    source = (desktop.NATIVE / "outlook" / "OutlookSession.cs").read_text(encoding="utf-8")
    assert source.count("CreateCoreWebView2ControllerAsync") == 1
    assert source.index("core.IsMuted = false;") < source.index(
        'core.Navigate("https://outlook.office.com/mail/")'
    )
    assert '{ "audio_muted", controller.CoreWebView2.IsMuted }' in source
