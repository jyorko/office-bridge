"""Exercise the browser-side guards against an isolated local Teams-shaped page."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "src/office_bridge/native/teams/chat.js").read_text(encoding="utf-8")
CHAT_ID = "19:" + "a" * 32 + "@thread.v2"
TITLE = "Example Group"


@pytest.fixture
def page():
    executable = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "robocorp/playwright/chromium-1254/chrome-win64/chrome.exe"
    )
    if not executable.is_file():
        pytest.skip("isolated Chromium test browser is not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        page = browser.new_page()
        html = f"""<button data-tid="me-control-avatar-trigger" aria-label="person@example.invalid"></button>
        <div data-tid="app-bar-wrapper"><button aria-label="Chat"></button></div>
        <div role="treeitem" data-fui-tree-item-value="folder|{CHAT_ID}" onclick="window.navs=(window.navs||0)+1"><span>{TITLE}</span></div>
        <div id="chat-header-{CHAT_ID}"><h2 data-tid="chat-title">{TITLE}</h2></div>
        <div data-tid="ckeditor" contenteditable="true"><br></div>
        <button data-tid="newMessageCommands-send" onclick="window.clicks=(window.clicks||0)+1">Send</button>"""
        page.route(
            "https://teams.cloud.microsoft/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body=html),
        )
        page.goto("https://teams.cloud.microsoft/")
        yield page
        browser.close()


def run(page, phase, body="hello", chat_id=CHAT_ID, title=TITLE):
    return page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)",
        [
            SCRIPT,
            {
                "phase": phase,
                "body": body,
                "chatId": chat_id,
                "title": title,
                "limit": 20,
                "requestId": "test-id",
                "authorName": "Example User",
                "navigationId": "test-id",
            },
        ],
    )


def test_wrong_chat_and_existing_draft_prevent_mutation(page):
    assert run(page, "navigate", chat_id="19:" + "b" * 32 + "@thread.v2")["state"] == "waiting"
    assert page.evaluate("window.navs || 0") == 0
    page.locator('[data-tid="ckeditor"]').fill("existing draft")
    assert run(page, "prepare")["state"] == "draft_exists"
    assert page.evaluate("window.clicks || 0") == 0
    assert page.locator('[data-tid="ckeditor"]').inner_text() == "existing draft"


def test_loading_chat_list_does_not_repeat_navigation(page):
    page.evaluate(
        """() => {
          document.querySelector('[role=treeitem]').remove();
          const tab=document.querySelector('[aria-label=Chat]');
          tab.onclick=()=>window.opens=(window.opens||0)+1;
        }"""
    )
    for _ in range(4):
        assert run(page, "navigate")["state"] == "waiting"
    assert page.evaluate("window.opens") == 1
    page.evaluate(
        """([id,title]) => {
          const row=document.createElement('div');
          row.setAttribute('role','treeitem');
          row.setAttribute('data-fui-tree-item-value','folder|'+id);
          const name=document.createElement('span');name.textContent=title;row.appendChild(name);
          row.onclick=()=>window.navs=(window.navs||0)+1;
          document.body.appendChild(row);
        }""",
        [CHAT_ID, TITLE],
    )
    assert run(page, "navigate")["state"] == "navigated"
    assert page.evaluate("window.opens") == 1
    assert page.evaluate("window.navs") == 1


def test_single_click_and_verified_loaded_history(page):
    assert run(page, "prepare")["state"] == "prepared"
    page.locator('[data-tid="ckeditor"]').fill("hello")
    assert run(page, "send")["state"] == "clicked"
    assert run(page, "send")["state"] == "failed"
    assert page.evaluate("window.clicks") == 1
    page.evaluate(
        """() => { const row=document.createElement('div');row.innerHTML='<span data-tid="message-author-name">Example User</span><time datetime="2026-11-12T19:06:15.584Z"></time><div data-tid="chat-pane-message" data-mid="5427607633819443908" id="message-body-5427607633819443908">hello</div>';document.body.appendChild(row); }"""
    )
    assert run(page, "observe")["state"] == "waiting"
    page.evaluate(
        """() => {const e=document.querySelector('[data-tid="chat-pane-message"]');e.id='message-body-1794510375584';e.setAttribute('data-mid','1794510375584');}"""
    )
    assert run(page, "observe")["message_id"] == "message-body-1794510375584"
    assert run(page, "read", 1)["messages"][0]["text"] == "hello"


def test_duplicate_titles_do_not_redirect_to_another_conversation(page):
    page.evaluate(
        """() => {const item=document.querySelector('[role="treeitem"]');const clone=item.cloneNode(true);clone.setAttribute('data-fui-tree-item-value','folder|19:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb@thread.v2');clone.onclick=()=>window.wrong=true;item.before(clone);}"""
    )
    assert run(page, "navigate")["state"] == "navigated"
    assert page.evaluate("window.navs") == 1
    assert not page.evaluate("!!window.wrong")


def test_self_chat_uses_its_pinned_conversation_and_preserves_draft(page):
    page.evaluate(
        """() => { document.querySelector('[role=treeitem]').setAttribute('data-fui-tree-item-value','folder|48:notes');document.querySelector('[role=treeitem] span').textContent='Example User';document.querySelector('[data-tid=chat-title]').textContent='Example User';document.querySelector('[id^=chat-header]').id='chat-header-48:notes';document.querySelector('[data-tid=ckeditor]').textContent='unfinished'; }"""
    )
    result = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)",
        [
            SCRIPT,
            {
                "phase": "prepare",
                "body": "hello",
                "chatId": "48:notes",
                "title": "Example User",
                "requestId": "self-1",
                "authorName": "Example User",
                "navigationId": "self-1",
            },
        ],
    )
    assert result["state"] == "draft_exists"
    assert page.locator('[data-tid="ckeditor"]').inner_text() == "unfinished"
    assert page.evaluate("window.clicks || 0") == 0


def test_generic_list_and_read_return_the_chat_reference(page):
    page.evaluate(
        """([id,title]) => {
          const row=document.querySelector('[role=treeitem]');
          row.setAttribute('data-fui-tree-item-value','folder|'+id);
          row.querySelector('span').textContent=title;
          const header=document.querySelector('[id^=chat-header]');
          header.id='chat-header-'+id;
          header.querySelector('[data-tid=chat-title]').textContent=title;
        }""",
        [CHAT_ID, TITLE],
    )
    listed = run(page, "list")
    assert listed["items"] == [{"chat_id": CHAT_ID, "title": TITLE}]
    verified = run(page, "read", chat_id=CHAT_ID)
    assert verified["state"] == "read"
    assert verified["chat_id"] == CHAT_ID
    assert verified["title"] == TITLE
