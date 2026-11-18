"""Outlook Web DOM actions run against isolated fixture pages."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIL_SCRIPT = (ROOT / "src/office_bridge/native/outlook/mail.js").read_text(encoding="utf-8")


@pytest.fixture
def page(isolated_browser):
    context = isolated_browser.new_context()
    try:
        page = context.new_page()
        yield page
    finally:
        context.close()


def run_script(page, request):
    script = MAIL_SCRIPT.replace("__REQUEST__", json.dumps(request))
    return page.evaluate(script)


def set_inbox(page, *, selected=True, with_composer=False):
    composer = (
        '<div role="textbox" aria-label="Message body" contenteditable="true">draft</div>'
        if with_composer
        else ""
    )
    selected_value = "true" if selected else "false"
    page.set_content(
        f"""<style>[role=option] span,[role=option] time{{display:block}}</style>
        <nav>
          <div role="treeitem" aria-label="Inbox" aria-selected="{selected_value}">Inbox</div>
          <div role="treeitem" aria-label="Sent Items">Sent Items</div>
        </nav>
        <main role="main">
          <div role="listbox" style="height:180px;overflow:auto">
            <div role="option" data-convid="conversation-001" aria-label="Unread">
              <span data-testid="sender">Contoso</span>
              <span>Quarterly update</span><span>Preview text</span>
              <time datetime="2026-11-18T12:00:00Z">12:00 PM</time>
            </div>
            <div role="option" data-convid="conversation-002">
              <span data-testid="sender">Fabrikam</span>
              <span>Meeting notes</span><span>Second preview</span>
              <time datetime="2026-11-17T11:00:00Z">Yesterday</time>
            </div>
          </div>
          <article role="document" data-itemid="message-001">Verified message body</article>
          {composer}
        </main>"""
    )


def test_scan_returns_exact_conversation_identity_and_bounded_metadata(page):
    set_inbox(page)
    result = run_script(page, {"phase": "scan", "step": "start"})
    assert result["state"] == "listed"
    assert [row["entry_id"] for row in result["rows"]] == [
        "conversation-001",
        "conversation-002",
    ]
    assert result["rows"][0]["lines"] == [
        "Contoso",
        "Quarterly update",
        "Preview text",
        "12:00 PM",
    ]
    assert result["rows"][0]["sender"]["display_name"] == "Contoso"
    assert result["rows"][0]["subject"] == "Quarterly update", result["rows"][0]
    assert result["rows"][0]["received_at"] == "2026-11-18T12:00:00.000Z"
    assert result["rows"][0]["unread"] is True
    assert result["rows"][0]["version"]["folder_entry_id"] == "inbox"
    assert "modified_at" not in result["rows"][0]["version"]


def test_icon_font_glyphs_do_not_replace_the_inbox_subject(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        """row => {
          const icon = document.createElement('span');
          icon.textContent = '\\uE1BB';
          row.insertBefore(icon, row.children[1]);
          const another = document.createElement('span');
          another.setAttribute('data-testid', 'subject-icon');
          another.textContent = '\\uEB68';
          row.insertBefore(another, row.children[2]);
        }"""
    )
    result = run_script(page, {"phase": "scan", "step": "start"})
    assert result["rows"][0]["subject"] == "Quarterly update"
    assert "\ue1bb" not in result["rows"][0]["lines"]
    assert "\ueb68" not in result["rows"][0]["lines"]


def test_scan_opens_only_the_unique_inbox_folder_and_preserves_composers(page):
    set_inbox(page, selected=False)
    navigated = run_script(page, {"phase": "scan", "step": "start"})
    assert navigated["state"] == "navigating_inbox"
    assert page.get_by_role("treeitem", name="Inbox").get_attribute("aria-selected") == "false"

    page.set_content(
        '<nav><div role="treeitem" aria-label="Inbox"></div></nav>'
        '<div role="dialog"><div contenteditable="true"></div><button>Send</button></div>'
    )
    blocked = run_script(page, {"phase": "scan", "step": "start"})
    assert blocked["state"] == "draft_exists"

    set_inbox(page, selected=False, with_composer=True)
    blocked = run_script(page, {"phase": "scan", "step": "start"})
    assert blocked["state"] == "draft_exists"
    assert page.get_by_role("treeitem", name="Inbox").get_attribute("aria-selected") == "false"


def test_scan_stops_when_inbox_identity_is_ambiguous(page):
    page.set_content(
        '<div role="treeitem" aria-label="Inbox"></div>'
        '<div role="treeitem" aria-label="Inbox favorites"></div>'
    )
    result = run_script(page, {"phase": "scan", "step": "start"})
    assert result["state"] == "inbox_ambiguous"


def test_open_and_read_require_the_exact_loaded_message_reference(page):
    set_inbox(page)
    opened = run_script(page, {"phase": "open", "conversation_id": "conversation-001"})
    assert opened == {"state": "opening", "conversation_id": "conversation-001"}
    missing = run_script(page, {"phase": "open", "conversation_id": "conversation-999"})
    assert missing["state"] == "not_visible"

    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    detail = run_script(page, {"phase": "read", "conversation_id": "conversation-001"})
    assert detail["state"] == "read"
    assert detail["item"]["entry_id"] == "conversation-001"
    assert detail["dom_message_id"] == "message-001"
    assert "message_id" not in detail
    assert detail["body_text"] == "Verified message body"
    assert detail["body_truncated"] is False


def test_read_without_dom_identity_leaves_the_exact_message_to_the_host(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    page.locator("[role=document]").evaluate("(element) => element.removeAttribute('data-itemid')")
    result = run_script(page, {"phase": "read", "conversation_id": "conversation-001"})
    assert result["state"] == "read"
    assert result["dom_message_id"] == ""


def test_read_refuses_a_dom_identity_that_contradicts_the_request(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    result = run_script(
        page,
        {"phase": "read", "conversation_id": "conversation-001", "message_id": "message-009"},
    )
    assert result["state"] == "message_identity_ambiguous"


def test_read_refuses_ambiguous_message_item_ids(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    page.locator("[role=document]").evaluate(
        """element => {
          const extra = document.createElement('span');
          extra.setAttribute('data-item-id', 'message-002');
          element.append(extra);
        }"""
    )
    result = run_script(page, {"phase": "read", "conversation_id": "conversation-001"})
    assert result["state"] == "message_identity_ambiguous"

    selected = run_script(
        page,
        {
            "phase": "read",
            "conversation_id": "conversation-001",
            "message_id": "message-001",
        },
    )
    assert selected["state"] == "message_identity_ambiguous"


def test_open_preserves_an_existing_composer(page):
    set_inbox(page, with_composer=True)
    opened = run_script(page, {"phase": "open", "conversation_id": "conversation-001"})
    assert opened["state"] == "draft_exists"
    assert page.get_by_role("textbox", name="Message body").inner_text() == "draft"


def test_read_refuses_multiple_visible_message_bodies(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    page.evaluate(
        """() => {
          const body = document.createElement('article');
          body.setAttribute('role', 'document');
          body.setAttribute('data-itemid', 'message-002');
          body.textContent = 'Another message';
          document.body.append(body);
        }"""
    )
    result = run_script(page, {"phase": "read", "conversation_id": "conversation-001"})
    assert result["state"] == "message_identity_ambiguous"


def test_identity_probe_opens_only_message_scoped_details_and_returns_counts(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    page.locator("[role=document]").evaluate(
        """element => {
          element.innerHTML = '<button aria-label="View message details"></button>Body';
          element.querySelector('button').addEventListener('click', () => {
            const dialog = document.createElement('div');
            dialog.setAttribute('role', 'dialog');
            dialog.innerHTML = '<dl><dt>Internet Message ID</dt>' +
              '<dd>&lt;synthetic@example.invalid&gt;</dd></dl>';
            document.body.append(dialog);
          });
        }"""
    )
    initial = run_script(page, {"phase": "identity_probe"})
    assert initial["details_control_count"] == 1
    assert initial["details_dialog_count"] == 0
    opened = run_script(page, {"phase": "identity_probe", "open_details": True})
    assert opened["state"] == "details_opened"
    inspected = run_script(page, {"phase": "identity_probe"})
    assert inspected["internet_message_id_count"] == 1
    assert "synthetic@example.invalid" not in json.dumps(inspected)


def test_identity_probe_does_not_open_details_when_composer_exists(page):
    set_inbox(page, with_composer=True)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    page.locator("[role=document]").evaluate(
        """element => {
          element.innerHTML = '<button aria-label="View message details"></button>';
          element.querySelector('button').addEventListener('click', () => {
            document.body.dataset.detailsOpened = 'true';
          });
        }"""
    )
    result = run_script(page, {"phase": "identity_probe", "open_details": True})
    assert result["state"] == "draft_exists"
    assert page.locator("body").get_attribute("data-details-opened") is None


def test_identity_probe_inspects_safe_ancestor_but_never_inbox_controls(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    page.locator("[role=document]").evaluate(
        """element => {
          const container = document.createElement('section');
          element.replaceWith(container);
          container.append(element);
          const button = document.createElement('button');
          button.setAttribute('aria-label', 'View message details');
          button.addEventListener('click', () => {
            document.body.dataset.messageDetailsClicked = 'true';
          });
          container.append(button);
          const unrelated = button.cloneNode();
          unrelated.addEventListener('click', () => {
            document.body.dataset.inboxControlClicked = 'true';
          });
          document.querySelector('[role=listbox]').append(unrelated);
        }"""
    )
    result = run_script(page, {"phase": "identity_probe"})
    assert result["safe_ancestor_depth"] == 1
    assert result["details_control_count"] == 1
    opened = run_script(page, {"phase": "identity_probe", "open_details": True})
    assert opened["state"] == "details_opened"
    assert page.locator("body").get_attribute("data-message-details-clicked") == "true"
    assert page.locator("body").get_attribute("data-inbox-control-clicked") is None


def test_identity_probe_stops_before_other_message_bodies(page):
    set_inbox(page)
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(element) => element.setAttribute('aria-selected', 'true')"
    )
    page.locator("[role=document]").evaluate(
        """element => {
          const container = document.createElement('section');
          element.replaceWith(container);
          container.append(element);
          const other = document.createElement('div');
          other.setAttribute('role', 'document');
          other.textContent = 'Other message';
          container.append(other);
          const button = document.createElement('button');
          button.setAttribute('aria-label', 'View message details');
          container.append(button);
        }"""
    )
    result = run_script(page, {"phase": "identity_probe"})
    assert result["safe_ancestor_depth"] == 0
    assert result["details_control_count"] == 0


def test_reply_click_requires_exact_message_and_preserves_existing_composer(page):
    set_inbox(page)
    card = page.locator("[role=document]")
    card.evaluate(
        """element => {
          element.innerHTML = '<button>Reply</button><button>Reply all</button>';
          element.querySelector('button:last-child').addEventListener('click', () => {
            const dialog = document.createElement('div');
            dialog.setAttribute('role', 'dialog');
            dialog.innerHTML = '<div contenteditable="true" aria-label="Message body"></div>' +
              '<button>Send</button>';
            document.body.append(dialog);
          });
        }"""
    )
    result = run_script(
        page,
        {
            "phase": "reply",
            "message_id": "message-001",
            "reply_all": True,
        },
    )
    assert result["state"] == "composer_opening"
    assert page.get_by_role("dialog").count() == 1

    blocked = run_script(
        page,
        {
            "phase": "reply",
            "message_id": "message-001",
            "reply_all": False,
        },
    )
    assert blocked["state"] == "draft_exists"


def test_create_draft_uses_resolved_recipient_and_returns_exact_draft_id(page):
    page.set_content(
        """<button aria-label="New mail"></button>
        <script>
          document.querySelector('button').addEventListener('click', () => {
            const dialog = document.createElement('div');
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('data-itemid', 'draft-001');
            dialog.setAttribute('data-convid', 'conversation-draft-001');
            dialog.innerHTML = '<input aria-label="To">' +
              '<input aria-label="Subject">' +
              '<div contenteditable="true" aria-label="Message body"></div>' +
              '<button>Send</button><button aria-label="Close"></button>' +
              '<span aria-label="Saved"></span>';
            document.body.append(dialog);
          });
        </script>"""
    )
    opened = run_script(page, {"phase": "new_mail"})
    assert opened["state"] == "composer_opening"
    filled = run_script(
        page,
        {
            "phase": "fill_draft",
            "recipient_email": "self@example.invalid",
            "subject": "Synthetic subject",
            "body_text": "<b>literal text</b>",
        },
    )
    assert filled["state"] == "draft_filled"
    page.evaluate(
        """() => {
          const option = document.createElement('div');
          option.setAttribute('role', 'option');
          option.textContent = 'Self self@example.invalid';
          option.addEventListener('click', () => {
            const chip = document.createElement('span');
            chip.setAttribute('data-email', 'self@example.invalid');
            chip.textContent = 'Self';
            document.querySelector('[role=dialog]').append(chip);
            option.remove();
          });
          document.body.append(option);
        }"""
    )
    selected = run_script(
        page,
        {"phase": "select_recipient", "recipient_email": "self@example.invalid"},
    )
    assert selected["state"] == "recipient_selected"
    snapshot = run_script(page, {"phase": "composer_state"})
    assert snapshot["state"] == "composer_ready"
    assert snapshot["entry_id"] == "draft-001"
    assert snapshot["conversation_id"] == "conversation-draft-001"
    assert snapshot["recipients"] == [
        {
            "display_name": "Self",
            "smtp_address": "self@example.invalid",
            "recipient_type": 1,
        }
    ]
    assert snapshot["body_text"] == "<b>literal text</b>"
    saved = run_script(page, {"phase": "close_saved_draft"})
    assert saved["state"] == "draft_saved"
    assert saved["draft"]["entry_id"] == "draft-001"


def test_new_mail_never_replaces_an_existing_composer(page):
    page.set_content(
        '<button aria-label="New mail"></button>'
        '<div role="dialog"><div contenteditable="true" aria-label="Message body"></div>'
        "<button>Send</button></div>"
    )
    result = run_script(page, {"phase": "new_mail"})
    assert result["state"] == "draft_exists"


def test_reply_without_dom_item_id_uses_only_the_verified_selected_conversation(page):
    set_inbox(page)
    page.locator("[role=document]").evaluate(
        """body => {
          body.removeAttribute('data-itemid');
          const button = document.createElement('button');
          button.setAttribute('aria-label', 'Reply');
          button.textContent = 'Reply';
          button.addEventListener('click', () => document.body.dataset.replyClicks = '1');
          body.parentElement.append(button);
        }"""
    )
    page.locator('[data-convid="conversation-001"]').evaluate(
        "(row) => row.setAttribute('aria-selected', 'true')"
    )
    assert (
        run_script(
            page,
            {"phase": "reply", "message_id": "service-item", "conversation_id": "conversation-002"},
        )["state"]
        == "message_not_found"
    )
    assert page.locator("body").get_attribute("data-reply-clicks") is None
    assert (
        run_script(
            page,
            {"phase": "reply", "message_id": "service-item", "conversation_id": "conversation-001"},
        )["state"]
        == "composer_opening"
    )
    assert page.locator("body").get_attribute("data-reply-clicks") == "1"


def test_send_rejects_changed_body_before_clicking(page):
    page.set_content(
        '<div role="dialog"><input aria-label="Subject" value="Subject">'
        '<div aria-label="Message body" contenteditable="true">Edited body</div>'
        '<span data-email="self@example.invalid">Self</span><button>Send</button></div>'
        '<script>document.querySelector("button").onclick = () => '
        'document.body.dataset.sendClicks = "1";</script>'
    )
    before = run_script(page, {"phase": "composer_state"})
    args = {
        "phase": "send",
        "subject": "Subject",
        "body_text": "Inspected body",
        "recipients": before["recipients"],
    }
    assert run_script(page, args)["state"] == "stale_draft"
    assert page.locator("body").get_attribute("data-send-clicks") is None
    assert run_script(page, {**args, "body_text": "Edited body"})["state"] == "send_clicked"
    assert page.locator("body").get_attribute("data-send-clicks") == "1"
    assert page.get_by_role("dialog").count() == 1
