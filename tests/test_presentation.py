from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from actions import Response

import office_presentation_actions
import outlook_actions
import teams_actions
from office_bridge import canvas_dispatch, presentation
from office_bridge.outlook.models import MessageRef, SendReceipt
from office_bridge.teams.models import GroupReceipt, WebChatHistory, WebChatMessage, WebChatRef

CHAT = WebChatRef(chat_id="19:canvas-fixture@thread.v2", title="Project chat")


@pytest.mark.parametrize("status", ["observed", "already_observed"])
def test_teams_send_keeps_receipt_and_displays_observed_status(monkeypatch, status):
    receipt = GroupReceipt(
        status=status,
        chat=CHAT,
        message_id="fixture",
        time="10:15",
        body_text="Ready <now> & reviewed",
    )
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
    assert card.body == receipt.body_text
    assert "Ready &lt;now&gt; &amp; reviewed" in result.presentation.html
    assert result.presentation.view.detail == ""


def test_old_teams_receipt_does_not_backfill_text_from_a_new_request(monkeypatch):
    receipt = GroupReceipt(status="already_observed", chat=CHAT, message_id="fixture", time="10:15")
    monkeypatch.setattr(teams_actions.service, "send_chat", lambda *args: receipt)
    result = teams_actions.teams_web_send_chat(CHAT, "Do not invent this text", "fixture")
    assert result.presentation.view.cards[0].body == ""
    assert "Message text is unavailable" in result.presentation.view.detail
    assert "Do not invent this text" not in result.presentation.html


def test_teams_receipt_keeps_full_text_and_marks_only_unique_verified_mentions():
    body = "Alex Morgan: Please review this. Alex Morgan is the owner."
    card = presentation.Card(
        kind="receipt",
        heading="Project review",
        body=body,
        facts={"Verified mentions": "Alex Morgan, Mira Chen"},
    )
    display = presentation.present(
        presentation.View(source="Teams", title="Observed in Teams", cards=[card])
    )
    assert card.body == body
    assert 'class="preview"' not in display.html
    assert "<details" not in display.html
    assert display.html.count('class="verified-mention"') == 2
    receipt_text = display.html.partition('receipt-body">')[2].partition("</div>")[0]
    assert "<span class=" not in receipt_text
    assert body in display.html

    card.body = "Please ask Alex Morgan to review."
    display = presentation.present(
        presentation.View(source="Teams", title="Observed in Teams", cards=[card])
    )
    assert display.html.count('class="verified-mention"') == 3
    assert "Observed in Teams" in display.html
    assert "Project review" in display.html


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
        return receipt

    def fail(*args):
        raise failure

    monkeypatch.setattr(outlook_actions, "submit_outlook_draft", run)
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


def test_unnamed_teams_messages_have_distinct_rows_and_keep_chat_scope():
    view = presentation.message_view(
        {
            "chat": {"exact_tree_label": "Design review"},
            "messages": [{"text": "First note"}, {"text": "Second note"}],
            "captured_at": "2026-11-18T10:00:00Z",
        }
    )
    assert view.title == "Design review"
    assert [card.heading for card in view.cards] == ["Message 1", "Message 2"]
    assert "2 loaded messages" in view.detail
    assert "Captured" in view.detail


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


def test_browser_fallback_uses_existing_view_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFICE_CANVAS_SIGNING_KEY", "fixture-window-key")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    view = presentation.View(
        cards=[{"kind": "mail", "heading": "Agenda", "body": "<Review> & notes"}]
    )
    existing = office_presentation_actions.office_present_messages(view)
    seen = []

    def open_window(presentation):
        seen.append(presentation)
        return {"view_id": presentation.view_id, "opened": True}

    monkeypatch.setattr(office_presentation_actions, "open_window", open_window)
    receipt = office_presentation_actions.office_open_message_view(
        existing.window, existing.view_id
    )
    assert receipt.opened
    assert seen[0].view_id == existing.view_id
    assert seen[0].html == existing.html


def test_window_failure_exposes_only_a_known_explanation():
    message = canvas_dispatch.failure_message(
        {"error": "Desktop operation failed: draft_exists. Private path C:/private/mail"}
    )
    assert "open draft" in message
    assert "private" not in message
    assert "unconfirmed" not in message
    assert "sensitive" not in canvas_dispatch.failure_message({"error": "sensitive text"})


def test_window_dispatch_binds_source_version_view_and_instance(monkeypatch):
    monkeypatch.setenv("OFFICE_CANVAS_SIGNING_KEY", "fixture-key")
    monkeypatch.setenv("OFFICE_CANVAS_ROOT", "fixture-root")
    monkeypatch.setattr(
        canvas_dispatch,
        "server_record",
        lambda _: {"instance": "fixture", "mcp_url": "http://127.0.0.1:1/mcp"},
    )
    source = MessageRef(entry_id="fixture-mail", store_id="fixture-store")
    version = dict(
        modified_at="2026-11-18T09:00:00Z",
        subject="Agenda",
        unread=False,
        folder_entry_id="fixture-folder",
        flagged=False,
        importance="normal",
    )
    if "content_complete" in presentation.MessageVersion.model_fields:
        version.update(content_complete=True, version_digest="b" * 64)
    display = presentation.present(
        presentation.View(source="Outlook", cards=[{"heading": "Agenda", "kind": "mail"}])
    )
    display.mail = presentation.ReplyToMail(source=source, version=version)
    display.window_proof = display.proof()
    args = dict(
        source=source.model_dump(),
        expected_source=display.mail.version.model_dump(mode="json"),
        reply_all=True,
        body_text="Fixture text",
    )
    click = dict(
        display=display.model_dump(mode="json"),
        instance="fixture",
        name="outlook_create_reply_draft",
        arguments=args,
    )
    runtime = MagicMock()
    runtime.post.return_value.status_code = 200
    runtime.post.return_value.json.return_value = {"result": {"subject": "Saved fixture"}}
    monkeypatch.setattr(canvas_dispatch.requests, "Session", lambda: runtime)
    runtime.__enter__.return_value = runtime
    for bad in (
        {**click, "name": "outlook_send_draft"},
        {**click, "instance": "different"},
        {**click, "arguments": {**args, "source": {**args["source"], "entry_id": "other"}}},
        {
            **click,
            "arguments": {**args, "expected_source": {**args["expected_source"], "unread": True}},
        },
        {**click, "display": {**click["display"], "view_id": "different"}},
    ):
        with pytest.raises(ValueError):
            canvas_dispatch.dispatch(bad)
    runtime.post.assert_not_called()
    assert canvas_dispatch.dispatch(click) == {"result": {"subject": "Saved fixture"}}
    runtime.post.assert_called_once_with(
        "http://127.0.0.1:1/api/actions/desktop-office/outlook-create-reply-draft/run",
        json=args,
        timeout=(3, 65),
        allow_redirects=False,
    )


def test_conversation_read_requires_resolved_and_complete_version_to_reply():
    ref = SimpleNamespace(store_id="store", conversation_id="conversation", entry_id=None)
    version = SimpleNamespace(content_complete=True, version_digest="a" * 64)
    assert presentation.can_read(ref)
    assert not presentation.can_reply(ref, version)
    ref.entry_id = "individual-message"
    assert presentation.can_reply(ref, version)
    version.content_complete = False
    assert not presentation.can_reply(ref, version)
    version.content_complete = True
    message = SimpleNamespace(ref=ref, version=version, body_truncated=False)
    assert not presentation.can_offer_reply(message)
    message.can_create_reply_draft = True
    assert presentation.can_offer_reply(message)
