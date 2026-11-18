from datetime import datetime, timezone

import pytest
from actions import Response

import office_presentation_actions
import outlook_actions
import teams_actions
from office_bridge import presentation
from office_bridge.outlook.models import MessageRef, SendReceipt
from office_bridge.teams.models import GroupReceipt, WebChatHistory, WebChatMessage, WebChatRef

CHAT = WebChatRef(chat_id="19:canvas-fixture@thread.v2", title="Project chat")


@pytest.mark.parametrize("status", ["observed", "already_observed"])
def test_teams_send_keeps_receipt_and_displays_observed_status(monkeypatch, status):
    receipt = GroupReceipt(status=status, chat=CHAT, message_id="fixture", time="10:15")
    calls = []

    def send(*args):
        calls.append(args)
        return receipt

    monkeypatch.setattr(teams_actions.service, "send_chat", send)
    result = teams_actions.teams_web_send_chat(CHAT, "Ready <now> & reviewed", "fixture")
    assert len(calls) == 1
    assert result.result == receipt
    card = result.presentation.view.cards[0]
    assert card.kind == "receipt"
    assert card.heading == CHAT.title
    assert card.timestamp == "10:15"
    assert (
        result.presentation.view.title
        == {
            "observed": "Observed in Teams",
            "already_observed": "Previously observed in Teams",
        }[status]
    )
    assert "Ready" not in result.presentation.html


@pytest.mark.parametrize("failure", [OSError("missing asset"), RuntimeError("render failed")])
def test_display_failure_keeps_completed_outlook_send(monkeypatch, failure):
    receipt = SendReceipt(
        draft=MessageRef(entry_id="fixture", store_id="fixture"),
        status="submitted",
        submitted_at=datetime(2026, 11, 18, tzinfo=timezone.utc),
        subject="Review <complete>",
        body_text="Checklist & notes",
    )
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(result=receipt)

    def fail(*args):
        raise failure

    monkeypatch.setattr(outlook_actions.desktop, "run", run)
    monkeypatch.setattr(presentation, "present", fail)
    result = outlook_actions.outlook_send_draft(receipt.draft, "a" * 64)
    assert len(calls) == 1
    assert result.result == receipt
    assert result.error is None
    assert result.presentation is None


def test_chat_read_escapes_html_without_disclosing_routing_fields(monkeypatch):
    history = WebChatHistory(
        chat=CHAT,
        scope="currently_loaded_history",
        messages=[
            WebChatMessage(
                id="private-message-id",
                author="Alex <Morgan>",
                time="10:45",
                text="<img src=x onerror=alert(1)> & reviewed",
            )
        ],
    )
    monkeypatch.setattr(teams_actions.service, "read_chat", lambda *args: history)
    result = teams_actions.teams_web_read_chat(CHAT)
    assert result.result == history
    assert result.presentation.view.cards[0].body == history.messages[0].text
    assert "&lt;img src=x onerror=alert(1)&gt;" in result.presentation.html
    assert CHAT.chat_id not in result.presentation.html
    assert history.messages[0].id not in result.presentation.html
    assert "<script>" not in result.presentation.html


def test_mail_and_empty_and_partial_views():
    page = {
        "items": [
            {
                "sender": {"display_name": "Alex", "smtp_address": None},
                "subject": "",
                "received_at": "2026-11-18T10:00:00Z",
            }
        ],
        "truncated": True,
        "scanned_count": 500,
    }
    view = presentation.message_view(page)
    assert view.cards[0].heading == "(No subject)"
    assert view.title == "Inbox"
    assert view.detail == "1 message · 500 scanned · Partial inbox results"
    assert presentation.message_view({"items": [], "truncated": False}).empty == "No messages found"


def test_preview_boundary_and_unread_are_shared_display_fields():
    short = presentation.Card(kind="chat", heading="Alex", body="🚀" * 400)
    long = presentation.Card(kind="chat", heading="Alex", body="🚀" * 401)
    view = presentation.View(
        cards=[long, presentation.Card(kind="mail", heading="Agenda", unread=True)]
    )
    html = presentation.present(view).html
    assert 'class="preview"' not in presentation.present(presentation.View(cards=[short])).html
    assert "🚀" * 280 + "…" in html
    assert 'class="unread">Unread' in html
    assert str(long.body) in html


def test_unrecognized_result_keeps_original_without_a_success_view():
    result = MessageRef(entry_id="fixture", store_id="fixture")
    response = presentation.with_presentation(Response(result=result))
    assert response.result == result
    assert response.presentation is None
    with pytest.raises(ValueError, match="Unrecognized"):
        presentation.message_view({"submitted_at": "2026-11-18T10:00:00Z"})


@pytest.mark.parametrize(
    "preview", [{}, {"subject": "Proofs", "body_text": "Approved", "body_truncated": True}]
)
def test_cached_outlook_receipt_preserves_the_original_outcome(preview):
    receipt = SendReceipt(
        draft=MessageRef(entry_id="fixture", store_id="fixture"),
        status="already_submitted",
        submitted_at=datetime(2026, 11, 18, tzinfo=timezone.utc),
        **preview,
    )
    response = presentation.with_presentation(Response(result=receipt))
    assert response.result == receipt
    assert response.presentation.view.title == "Previously submitted to Outlook"
    card = response.presentation.view.cards[0]
    assert card.body == preview.get("body_text", "")
    assert card.body_truncated == preview.get("body_truncated", False)


def test_unknown_outlook_receipt_status_is_never_displayed_as_success():
    with pytest.raises(ValueError, match="Unrecognized"):
        presentation.message_view(
            {"draft": {}, "status": "outcome_unknown", "submitted_at": "09:00"}
        )


def test_browser_fallback_uses_existing_view_identity(monkeypatch):
    view = presentation.View(
        cards=[{"kind": "mail", "heading": "Agenda", "body": "<Review> & notes"}]
    )
    existing = office_presentation_actions.office_present_messages(view)
    seen = []

    def open_window(presentation):
        seen.append(presentation)
        return {"view_id": presentation.view_id, "opened": True}

    monkeypatch.setattr(office_presentation_actions, "open_window", open_window)
    receipt = office_presentation_actions.office_open_message_view(existing.view_id, view)
    assert receipt.opened
    assert seen[0].view_id == existing.view_id
    assert seen[0].html == existing.html
