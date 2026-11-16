"""Exercise the browser-side guards against an isolated local Teams-shaped page."""

from pathlib import Path

import pytest

from office_bridge.teams.formatting import format_body

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "src/office_bridge/native/teams/chat.js").read_text(encoding="utf-8")
RENDER_NODES = (ROOT / "src/office_bridge/native/teams/formatting.js").read_text(encoding="utf-8")
CHAT_ID = "19:" + "a" * 32 + "@thread.v2"
TITLE = "Example Group"


@pytest.fixture
def page(isolated_browser):
    context = isolated_browser.new_context()
    try:
        page = context.new_page()
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
    finally:
        context.close()


EMPTY_FORMATTING = {
    "bold": [],
    "italic": [],
    "code": [],
    "links": [],
    "unordered_list": False,
    "ordered_list": False,
}


def test_message_reactions_and_links_are_bound_to_exact_message(page):
    message_id = "message-body-1700000000000"
    page.evaluate(
        """id=>{
          const item=document.createElement('div');item.dataset.tid='chat-pane-item';
          item.innerHTML='<div data-tid="chat-pane-message" id="'+id+'">Hello</div>'
            +'<div role="toolbar" aria-label="Reactions and message actions">'
            +'<button aria-label="Like" aria-pressed="false" onclick="window.reactions=(window.reactions||0)+1;this.setAttribute(\\'aria-pressed\\',\\'true\\')">Like</button></div>';
          document.body.append(item);
        }""",
        message_id,
    )

    def operate(phase, **overrides):
        request = {
            "phase": phase,
            "chatId": CHAT_ID,
            "title": TITLE,
            "messageId": message_id,
            "expectedText": "Hello",
            "reaction": "Like",
            "requestId": "one",
            **overrides,
        }
        return page.evaluate(
            "([source,request])=>(0,eval)('('+source+')')(request)", [SCRIPT, request]
        )

    assert operate("message_link")["url"].startswith("https://teams.microsoft.com/l/message/")
    assert operate("message_reactions")["reactions"] == [{"name": "Like", "selected": False}]
    assert operate("message_react", expectedText="Wrong")["state"] == "message_changed"
    assert operate("message_react", chatId="19:wrong@thread.v2")["state"] == "waiting"
    assert page.evaluate("window.reactions||0") == 0
    page.evaluate("window.reactionNow=10000;Date.now=()=>window.reactionNow")
    assert operate("message_react")["state"] == "reaction_clicked"
    assert operate("message_observe")["state"] == "waiting"
    page.evaluate("window.reactionNow+=2100")
    assert operate("message_observe")["state"] == "reacted"
    assert operate("message_react")["state"] == "already_reacted"
    assert page.evaluate("window.reactions") == 1
    page.locator('button[aria-label="Like"]').evaluate("e=>e.setAttribute('aria-pressed','false')")
    assert operate("message_react")["state"] == "reaction_uncertain"
    assert page.evaluate("window.reactions") == 1


def test_toolbar_reaction_checks_content_after_reaction_summary_changes(page):
    message_id = "message-body-1700000000000"
    page.evaluate(
        """id=>{
          const item=document.createElement('div');item.dataset.tid='chat-pane-item';
          item.innerHTML='<div data-tid="chat-pane-message" id="'+id+'">'
            +'<div data-message-content>Hello</div></div>'
            +'<div role="toolbar" aria-label="Reactions and message actions">'
            +'<button aria-label="Like" aria-pressed="false">Like</button></div>';
          document.body.append(item);
          item.querySelector('button').onclick=function(){
            this.setAttribute('aria-pressed','true');
            document.getElementById(id).insertAdjacentHTML('beforeend',
              '<div data-tid="diverse-reaction-summary">1 Like reaction.</div>');
          };
          window.reactionNow=10000;Date.now=()=>window.reactionNow;
        }""",
        message_id,
    )

    def operate(phase):
        return page.evaluate(
            "([source,request])=>(0,eval)('('+source+')')(request)",
            [
                SCRIPT,
                {
                    "phase": phase,
                    "chatId": CHAT_ID,
                    "title": TITLE,
                    "messageId": message_id,
                    "expectedText": "Hello",
                    "reaction": "Like",
                    "requestId": "summary",
                },
            ],
        )

    assert operate("message_react")["state"] == "reaction_clicked"
    assert operate("message_observe")["state"] == "waiting"
    page.evaluate("window.reactionNow+=2100")
    assert operate("message_observe")["state"] == "reacted"
    page.locator("[data-message-content]").evaluate("e=>e.innerText='Edited'")
    assert operate("message_observe")["state"] == "reaction_uncertain"


def test_picker_reaction_survives_summary_text_and_toolbar_dismissal(page):
    message_id = "message-body-1700000000000"
    page.evaluate(
        """id=>{
          const item=document.createElement('div');item.dataset.tid='chat-pane-item';
          item.innerHTML='<div data-tid="chat-pane-message" id="'+id+'">'
            +'<button data-tid="message-actions-menu-hidden-button"></button>'
            +'<div data-message-content>Hello</div></div>';
          document.body.append(item);
          const toolbar=document.createElement('div');
          toolbar.setAttribute('role','toolbar');
          toolbar.setAttribute('aria-label','Reactions and message actions');
          toolbar.innerHTML='<button data-tid="expanded-reactions-picker-entry">More</button>';
          document.body.append(toolbar);
          toolbar.firstChild.onclick=()=>{
            const picker=document.createElement('div');
            picker.id='expanded-reaction-picker-popup-surface';
            picker.innerHTML='<button aria-label="Hundred points" data-tid="emoticon-button-1f4af_hundredpointssymbol">'
              +'<img itemid="1f4af_hundredpointssymbol"></button>';
            picker.firstChild.onclick=()=>{
              window.reactions=(window.reactions||0)+1;
              document.getElementById(id).insertAdjacentHTML('beforeend',
                '<button data-tid="diverse-reaction-pill-button" aria-pressed="true">'
                +'<img itemid="1f4af_hundredpointssymbol">1 Hundred points reaction.</button>');
              toolbar.remove();picker.remove();
            };
            document.body.append(picker);
          };
          window.reactionNow=10000;Date.now=()=>window.reactionNow;
        }""",
        message_id,
    )

    def operate(phase, **overrides):
        request = {
            "phase": phase,
            "chatId": CHAT_ID,
            "title": TITLE,
            "messageId": message_id,
            "expectedText": "Hello",
            "reaction": "Hundred points",
            "requestId": "hundred",
            **overrides,
        }
        return page.evaluate(
            "([source,request])=>(0,eval)('('+source+')')(request)", [SCRIPT, request]
        )

    assert operate("message_picker_open")["state"] == "reaction_unavailable"
    assert operate("message_open")["state"] == "toolbar_opening"
    assert operate("message_picker_open")["state"] == "picker_opening"
    assert operate("message_reactions")["reactions"] == [
        {"name": "Hundred points", "selected": False}
    ]
    assert operate("message_react")["state"] == "reaction_clicked"
    assert operate("message_observe")["state"] == "waiting"
    page.evaluate("window.reactionNow+=2100")
    assert operate("message_observe")["state"] == "reacted"
    assert page.evaluate("window.reactions") == 1
    page.evaluate(
        """()=>{
          const picker=document.createElement('div');
          picker.id='expanded-reaction-picker-popup-surface';
          picker.innerHTML='<button aria-label="Undo Hundred points" data-tid="emoticon-button-1f4af_hundredpointssymbol">'
            +'<img itemid="1f4af_hundredpointssymbol"></button>';
          picker.firstChild.onclick=()=>window.reactions++;
          document.body.append(picker);
        }"""
    )
    current_text = page.locator(f'[id="{message_id}"]').inner_text()
    assert operate("message_reactions", expectedText=current_text)["reactions"] == [
        {"name": "Hundred points", "selected": True}
    ]
    assert operate("message_react", expectedText=current_text)["state"] == "already_reacted"
    assert page.evaluate("window.reactions") == 1
    page.locator("[data-message-content]").evaluate("e=>e.innerText='Edited'")
    assert operate("message_observe")["state"] == "reaction_uncertain"


def run(
    page,
    phase,
    body="hello",
    chat_id=CHAT_ID,
    title=TITLE,
    message_id=None,
    body_format="plain",
    html=None,
    formatting=None,
    nodes=None,
    menu_action="",
    expected_text=None,
    limit=20,
):
    if body_format == "markdown" and nodes is None and html is None:
        prepared = format_body(body, body_format)
        html, formatting, nodes = prepared.html, prepared.formatting, prepared.nodes
    return page.evaluate(
        "([source,renderer,request]) => { const render=(0,eval)('('+renderer+')'); "
        "return (0,eval)('('+source+')')(request,render); }",
        [
            SCRIPT,
            RENDER_NODES,
            {
                "phase": phase,
                "body": body,
                "bodyFormat": body_format,
                "html": html,
                "nodes": nodes,
                "formatting": formatting or EMPTY_FORMATTING,
                "chatId": chat_id,
                "title": title,
                "limit": limit,
                "requestId": "test-id",
                "authorName": "Example User",
                "navigationId": "test-id",
                "messageId": message_id,
                "menuAction": menu_action,
                "expectedText": expected_text,
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


def test_navigation_rechecks_workspace_draft_after_opening_chat_list(page):
    page.evaluate(
        """([chatId,title]) => {
          document.querySelector('[role="treeitem"]').remove();
          document.querySelector('[id^="chat-header-"]').remove();
          document.querySelector('[data-tid="ckeditor"]').remove();
          const tab=document.querySelector('[aria-label="Chat"]');
          tab.onclick=()=>{
            window.opens=(window.opens||0)+1;
            const row=document.createElement('div');
            row.setAttribute('role','treeitem');
            row.setAttribute('data-fui-tree-item-value','folder|'+chatId);
            const label=document.createElement('span');label.textContent=title;row.append(label);
            row.onclick=()=>window.navs=(window.navs||0)+1;
            document.body.append(row);
            const header=document.createElement('div');header.id='chat-header-'+chatId;
            const heading=document.createElement('h2');heading.setAttribute('data-tid','chat-title');
            heading.textContent=title;header.append(heading);document.body.append(header);
            const editor=document.createElement('div');editor.setAttribute('data-tid','ckeditor');
            editor.setAttribute('contenteditable','true');editor.textContent='restored workspace draft';
            document.body.append(editor);
          };
        }""",
        [CHAT_ID, TITLE],
    )
    assert run(page, "navigate")["state"] == "waiting"
    assert page.evaluate("window.opens") == 1
    assert page.evaluate("window.navs || 0") == 0
    assert run(page, "navigate")["state"] == "draft_exists"
    assert page.locator('[data-tid="ckeditor"]').inner_text() == "restored workspace draft"
    assert page.evaluate("window.clicks || 0") == 0


def test_workspace_ready_does_not_reopen_selected_chat_with_a_draft(page):
    tab = page.locator('[data-tid="app-bar-wrapper"] button[aria-label="Chat"]')
    tab.evaluate(
        "button => { button.setAttribute('aria-pressed','true'); "
        "button.onclick=()=>window.opens=(window.opens||0)+1; }"
    )
    editor = page.locator('[data-tid="ckeditor"]')
    editor.fill("existing workspace draft")
    result = run(page, "workspace_ready")
    assert result["state"] == "workspace_ready"
    assert result["selected"] is True
    assert result["dirty"] is True
    assert editor.inner_text() == "existing workspace draft"
    assert page.evaluate("window.opens || 0") == 0
    assert page.evaluate("window.clicks || 0") == 0


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


def test_read_reports_scroll_metadata_without_duplicating_message_text(page):
    page.locator("body").evaluate(
        """body => {
          const pane=document.createElement('div');
          pane.setAttribute('data-tid','message-list');
          pane.style.cssText='height:40px;overflow-y:auto;display:block';
          for(let i=1;i<=3;i++) {
            const wrapper=document.createElement('div');
            wrapper.style.height='30px';
            wrapper.innerHTML='<span data-tid="message-author-name">Example User</span>'+
              '<time datetime="2026-11-16T00:00:0'+i+'Z"></time>'+
              '<div data-tid="chat-pane-message" id="message-body-'+i+'">private message '+i+'</div>';
            pane.append(wrapper);
          }
          const older=document.createElement('button');
          older.setAttribute('aria-label','Load older messages');
          body.append(pane,older);
        }"""
    )
    result = run(page, "read", limit=3)
    history_ui = result["history_ui"]
    assert history_ui["loaded_count"] == 3
    assert history_ui["scroll_ancestors"][0]["tid"] == "message-list"
    assert history_ui["scroll_ancestors"][0]["overflow_y"] == "auto"
    assert history_ui["older_controls"] == [
        {"label": "Load older messages", "tid": "", "disabled": False}
    ]
    assert "private message" not in str(history_ui)


def test_read_scrolls_older_history_within_limit_and_preserves_draft(page):
    page.locator("body").evaluate(
        """body => {
          const pane=document.createElement('div');
          pane.setAttribute('data-tid','message-pane-list-viewport');
          pane.style.cssText='height:80px;overflow-y:auto;display:block';
          const append=(parent,start,end,prepend=false)=>{
            const fragment=document.createDocumentFragment();
            for(let i=start;i<=end;i++) {
              const wrapper=document.createElement('div');
              wrapper.style.height='30px';
              wrapper.innerHTML='<span data-tid="message-author-name">Example User</span>'+
                '<time datetime="2026-11-16T00:00:'+String(i).padStart(2,'0')+'Z"></time>'+
                '<div data-tid="chat-pane-message" id="message-body-'+i+'">Message '+i+'</div>';
              fragment.append(wrapper);
            }
            prepend ? parent.prepend(fragment) : parent.append(fragment);
          };
          append(pane,11,20);
          pane.addEventListener('scroll',()=>{
            if(pane.scrollTop===0&&!window.olderLoaded) {
              window.olderLoaded=true;
              append(pane,1,10,true);
              requestAnimationFrame(()=>pane.scrollTop=300);
            }
          });
          body.append(pane);
          pane.scrollTop=pane.scrollHeight;
        }"""
    )
    editor = page.locator('[data-tid="ckeditor"]')
    editor.fill("keep this unsent draft")
    editor.focus()
    first = run(page, "read", limit=15)
    assert first["state"] == "waiting"
    page.wait_for_timeout(200)
    result = run(page, "read", limit=15)

    assert result["state"] == "read"
    assert [message["id"] for message in result["messages"]] == [
        f"message-body-{i}" for i in range(6, 21)
    ]
    assert result["history_ui"]["scroll_requests"] == 1
    assert result["history_ui"]["collected_count"] == 15
    assert page.evaluate("window.olderLoaded") is True
    assert page.locator('[data-tid="ckeditor"]').inner_text() == "keep this unsent draft"
    assert page.evaluate("document.activeElement === document.querySelector('[data-tid=ckeditor]')")
    assert page.evaluate(
        "document.querySelector('[data-tid=message-pane-list-viewport]').scrollTop"
    ) == page.evaluate(
        "document.querySelector('[data-tid=message-pane-list-viewport]').scrollHeight - document.querySelector('[data-tid=message-pane-list-viewport]').clientHeight"
    )
    assert page.evaluate("window.clicks || 0") == 0


def test_read_stops_at_available_history_without_repeating_navigation(page):
    page.locator("body").evaluate(
        """body => {
          const pane=document.createElement('div');
          pane.setAttribute('data-tid','message-pane-list-viewport');
          pane.style.cssText='height:80px;overflow-y:auto;display:block';
          for(let i=1;i<=10;i++) {
            const wrapper=document.createElement('div');wrapper.style.height='30px';
            wrapper.innerHTML='<span data-tid="message-author-name">Example User</span>'+
              '<time datetime="2026-11-16T00:00:'+String(i).padStart(2,'0')+'Z"></time>'+
              '<div data-tid="chat-pane-message" id="message-body-'+i+'">Message '+i+'</div>';
            pane.append(wrapper);
          }
          body.append(pane);pane.scrollTop=pane.scrollHeight;
        }"""
    )
    editor = page.locator('[data-tid="ckeditor"]')
    editor.fill("preserve this draft")
    first = run(page, "read", limit=15)
    assert first["state"] == "waiting"
    page.wait_for_timeout(1900)
    result = run(page, "read", limit=15)
    assert result["state"] == "read"
    assert len(result["messages"]) == 10
    assert result["history_ui"]["scroll_requests"] == 1
    assert editor.inner_text() == "preserve this draft"
    assert page.evaluate("window.navs || 0") == 0


def test_read_does_not_scroll_when_chat_identity_is_ambiguous(page):
    page.locator("body").evaluate(
        """body => {
          const pane=document.createElement('div');
          pane.setAttribute('data-tid','message-pane-list-viewport');
          pane.style.cssText='height:40px;overflow-y:auto;display:block';
          for(let i=1;i<=4;i++) {
            const message=document.createElement('div');
            message.setAttribute('data-tid','chat-pane-message');
            message.id='message-body-'+i;message.textContent='Message '+i;
            pane.append(message);
          }
          body.append(pane);pane.scrollTop=pane.scrollHeight;
        }"""
    )
    pane = page.locator('[data-tid="message-pane-list-viewport"]')
    before = pane.evaluate("element => element.scrollTop")
    result = run(page, "read", chat_id="19:" + "b" * 32 + "@thread.v2", limit=15)
    assert result["state"] == "waiting"
    assert pane.evaluate("element => element.scrollTop") == before
    assert page.evaluate("window.__officeReadHistory || null") is None


def test_markdown_paste_validates_and_preserves_requested_formatting(page):
    body = "**Urgent** review [PR 378](https://example.invalid/pr/378)"
    prepared = format_body(body, "markdown")
    assert prepared.html is not None
    rich_args = {
        "body": prepared.body,
        "body_format": "markdown",
        "html": prepared.html,
        "formatting": prepared.formatting,
        "nodes": prepared.nodes,
    }
    assert (
        run(
            page,
            "validate_payload",
            **rich_args,
        )["state"]
        == "payload_valid"
    )
    page.locator('[data-tid="ckeditor"]').evaluate(
        "editor => editor.addEventListener('paste', event => { event.preventDefault(); editor.innerHTML=event.clipboardData.getData('text/html'); })"
    )
    page.evaluate("window.__officeNavigation={id:'test-id',opened:true}")
    assert (
        run(
            page,
            "prepare",
            **rich_args,
        )["state"]
        == "prepared"
    )
    assert (
        run(
            page,
            "paste",
            **rich_args,
        )["state"]
        == "pasted"
    )
    assert page.locator('[data-tid="ckeditor"] strong').inner_text() == "Urgent"
    assert page.locator('[data-tid="ckeditor"] a').get_attribute("href") == (
        "https://example.invalid/pr/378"
    )
    assert (
        run(
            page,
            "send",
            **rich_args,
        )["state"]
        == "clicked"
    )


def test_markdown_payload_rejects_unsafe_or_mismatched_nodes(page):
    prepared = format_body("**safe** text", "markdown")
    unsafe = [{"tag": "script", "attrs": {}, "children": [{"text": "unsafe"}]}]
    assert (
        run(
            page,
            "validate_payload",
            prepared.body,
            body_format="markdown",
            formatting=prepared.formatting,
            nodes=unsafe,
        )["state"]
        == "invalid_format"
    )
    mismatched = [{"tag": "strong", "attrs": {}, "children": [{"text": "different"}]}]
    assert (
        run(
            page,
            "validate_payload",
            prepared.body,
            body_format="markdown",
            formatting=prepared.formatting,
            nodes=mismatched,
        )["state"]
        == "invalid_format"
    )


def test_inspect_menu_requires_own_message_and_closes_after_snapshot(page):
    message_id = "message-body-1794521099818"
    page.evaluate(
        """([messageId]) => {
          const wrapper=document.createElement('div');
          wrapper.setAttribute('data-tid','chat-pane-item');
              wrapper.className='fui-ChatMyMessage';
          wrapper.innerHTML='<span data-tid="message-author-name">Example User</span><time datetime="2026-11-12T19:06:15.584Z"></time>';
          const message=document.createElement('div');
          message.id=messageId;message.setAttribute('data-tid','chat-pane-message');
          message.setAttribute('data-mid',messageId.slice('message-body-'.length));
          message.setAttribute('role','group');message.textContent='old text';
          const trigger=document.createElement('button');
          trigger.setAttribute('data-tid','message-actions-menu-hidden-button');
          trigger.setAttribute('aria-label','More message options');
          trigger.setAttribute('aria-controls','target-menu');
          trigger.setAttribute('aria-expanded','false');
          trigger.onclick=()=>{
            const existing=document.getElementById('target-menu');
            if(existing) {existing.remove();trigger.setAttribute('aria-expanded','false');}
            else {const menu=document.createElement('div');menu.id='target-menu';menu.setAttribute('role','menu');menu.innerHTML='<button role="menuitem">Edit</button><button role="menuitem">Delete</button>';document.body.append(menu);trigger.setAttribute('aria-expanded','true');}
          };
          message.append(trigger);wrapper.append(message);document.body.append(wrapper);
              const toolbar=document.createElement('div');
              toolbar.setAttribute('role','toolbar');toolbar.setAttribute('aria-label','Chat actions');
              toolbar.innerHTML='<button>Filter options</button>';document.body.append(toolbar);
        }""",
        [message_id],
    )
    opened = run(page, "inspect_message", message_id=message_id, menu_action="open")
    assert opened["state"] == "message_hover_target"
    assert opened["point"]["x"] > 0
    page.evaluate("document.querySelector('[data-tid=message-actions-menu-hidden-button]').click()")
    inspected = run(page, "inspect_message", message_id=message_id, menu_action="read")
    assert inspected["own_message"] is True
    assert inspected["text"] == "old text"
    assert inspected["menu"][0]["items"][0]["label"] == "Edit"
    assert any(item["role"] == "toolbar" for item in inspected["popup_candidates"])
    assert inspected["menu_closed"] is True


def test_same_author_name_without_own_marker_cannot_open_or_activate_edit_menu(page):
    message_id = "message-body-1794521099819"
    page.evaluate(
        """([messageId]) => {
          const wrapper=document.createElement('div');
          wrapper.setAttribute('data-tid','chat-pane-item');
          wrapper.innerHTML='<span data-tid="message-author-name">Example User</span><time datetime="2026-11-12T19:06:15.584Z"></time>';
          const message=document.createElement('div');
          message.id=messageId;message.setAttribute('data-tid','chat-pane-message');
          message.setAttribute('data-mid',messageId.slice('message-body-'.length));
          message.setAttribute('role','group');message.textContent='another user message';
          const trigger=document.createElement('button');
          trigger.setAttribute('data-tid','message-actions-menu-hidden-button');
          trigger.setAttribute('aria-label','More message options');
          trigger.onclick=()=>window.menuClicks=(window.menuClicks||0)+1;
          message.append(trigger);wrapper.append(message);document.body.append(wrapper);
        }""",
        [message_id],
    )

    inspected = run(page, "inspect_message", message_id=message_id)
    assert inspected["author"] == "Example User"
    assert inspected["own_message"] is False

    assert (
        run(page, "inspect_message", message_id=message_id, menu_action="open")["state"]
        == "not_own_message"
    )
    assert (
        run(page, "inspect_message", message_id=message_id, menu_action="activate")["state"]
        == "not_own_message"
    )
    assert page.evaluate("window.menuClicks || 0") == 0


def mount_editable_message(page, content):
    message_id = "message-body-1794509400000"
    page.evaluate(
        """([id, content]) => {
          document.querySelector('[data-tid=ckeditor]').id='main-composer';
          const item=document.createElement('div');item.className='fui-ChatMyMessage';
          item.innerHTML='<span data-tid="message-author-name">Example User</span><time datetime="2026-11-12T18:50:00.000Z"></time>';
          const message=document.createElement('div');message.id=id;
          message.setAttribute('data-tid','chat-pane-message');message.setAttribute('data-mid',id.slice(13));
          message.innerHTML=content;
          const trigger=document.createElement('button');trigger.setAttribute('data-tid','message-actions-menu-hidden-button');
          trigger.onclick=()=>{
            const toolbar=document.createElement('div');toolbar.setAttribute('role','toolbar');
            toolbar.setAttribute('aria-label','Reactions and message actions');
            const button=document.createElement('button');button.setAttribute('data-tid','message-actions-edit');
            button.onclick=()=>{
              toolbar.remove();
              const container=document.createElement('div');
              const editor=document.createElement('div');editor.id='inline-editor';
              editor.setAttribute('data-tid','ckeditor');editor.setAttribute('contenteditable','true');
              editor.innerHTML=content;
              let selectedRange=null;
              window.ckeditorCalls=[];
              editor.ckeditorInstance={
                isReadOnly:false,
                data:{
                  processor:{toView:html=>{window.ckeditorCalls.push('toView');const template=document.createElement('template');template.innerHTML=html;return template.content.cloneNode(true);}},
                  toModel:view=>{window.ckeditorCalls.push('toModel');return view.cloneNode(true);}
                },
                editing:{
                  view:{domConverter:{domRangeToView:range=>{window.ckeditorCalls.push('domRangeToView');return range.cloneRange();}}},
                  mapper:{toModelRange:range=>{window.ckeditorCalls.push('toModelRange');return range.cloneRange();}}
                },
                model:{
                  document:{selection:{}},
                  change:callback=>callback({setSelection:range=>{selectedRange=range;}}),
                  insertContent:(fragment)=>{
                    window.ckeditorCalls.push('insertContent');
                    selectedRange.deleteContents();
                    selectedRange.insertNode(fragment.cloneNode(true));
                  }
                }
              };
              editor.addEventListener('paste',()=>window.nativePasteEvents=(window.nativePasteEvents||0)+1);
              const cancel=document.createElement('button');cancel.setAttribute('data-tid','newMessageCommands-discard-draft');
              cancel.setAttribute('aria-label','Cancel');cancel.onclick=()=>container.remove();
              const done=document.createElement('button');done.setAttribute('data-tid','newMessageCommands-send');done.setAttribute('aria-label','Done');
              done.onclick=()=>{window.saves=(window.saves||0)+1;message.innerHTML=editor.innerHTML;container.remove();};
              container.append(editor,cancel,done);document.body.append(container);
            };
            toolbar.append(button);document.body.append(toolbar);
          };
          message.append(trigger);item.append(message);document.body.append(item);
        }""",
        [message_id, content],
    )
    return message_id, page.locator(f"#{message_id}").inner_text()


def test_edit_preserves_unchanged_leading_mention_and_saves_same_message_once(page):
    content = '<p><readonly itemtype="http://schema.skype.com/Mention"><mention mri="example">Example Person</mention></readonly> old text</p>'
    message_id, expected = mount_editable_message(page, content)
    prepared = format_body("Example Person **new** text", "markdown")
    args = {
        "message_id": message_id,
        "expected_text": expected,
        "body": prepared.body,
        "body_format": "markdown",
        "html": prepared.html,
        "formatting": prepared.formatting,
        "nodes": prepared.nodes,
    }
    for phase, state in [
        ("edit_begin", "toolbar_opening"),
        ("edit_open", "editor_opening"),
        ("edit_prepare", "editor_prepared"),
        ("edit_paste", "edit_pasted"),
        ("edit_save", "edit_clicked"),
    ]:
        result = run(page, phase, **args)
        assert result["state"] == state
    assert page.evaluate("window.ckeditorCalls") == [
        "toView",
        "toModel",
        "domRangeToView",
        "toModelRange",
        "insertContent",
    ]
    assert page.evaluate("window.nativePasteEvents || 0") == 0
    assert page.evaluate("window.saves") == 1
    assert run(page, "edit_save", **args)["state"] == "failed"
    assert page.evaluate("window.saves") == 1
    assert page.locator(f"#{message_id} mention").get_attribute("mri") == "example"
    assert page.locator(f"#{message_id} strong").inner_text() == "new"
    assert page.locator("#main-composer").inner_text().strip() == ""


def test_edit_uses_ckeditor_model_api_for_replacement(page):
    message_id, expected = mount_editable_message(page, "<p>before</p><p>second paragraph</p>")
    args = {"message_id": message_id, "expected_text": expected, "body": "after"}
    assert run(page, "edit_begin", **args)["state"] == "toolbar_opening"
    assert run(page, "edit_open", **args)["state"] == "editor_opening"
    assert run(page, "edit_prepare", **args)["state"] == "editor_prepared"
    assert run(page, "edit_paste", **args)["state"] == "edit_pasted"
    assert page.evaluate("window.ckeditorCalls") == [
        "toView",
        "toModel",
        "domRangeToView",
        "toModelRange",
        "insertContent",
    ]
    assert page.evaluate("window.nativePasteEvents || 0") == 0
    assert page.locator("#inline-editor").inner_text() == "after"
    assert page.evaluate("window.saves || 0") == 0


def test_edit_uses_message_projection_after_scroll_changes_layout_text(page):
    message_id, expected = mount_editable_message(
        page, "<p>Office Bridge rich-text</p><p>verification.Repository</p>"
    )
    args = {"message_id": message_id, "expected_text": expected, "body": "after"}
    assert run(page, "edit_begin", **args)["state"] == "toolbar_opening"
    page.evaluate(
        """id => {
          const message=document.getElementById(id);
          Object.defineProperty(message, 'innerText', {configurable:true, get:()=> 'Office Bridge rich-text verification.Repository'});
        }""",
        message_id,
    )
    assert run(page, "edit_open", **args)["state"] == "editor_opening"
    assert run(page, "edit_prepare", **args)["state"] == "editor_prepared"


def test_changed_inline_editor_prevents_save_and_preserves_both_composers(page):
    message_id, expected = mount_editable_message(page, "<p>before</p>")
    args = {"message_id": message_id, "expected_text": expected, "body": "after"}
    assert run(page, "edit_begin", **args)["state"] == "toolbar_opening"
    assert run(page, "edit_open", **args)["state"] == "editor_opening"
    page.locator("#inline-editor").fill("changed outside this request")
    assert run(page, "edit_prepare", **args)["state"] == "editor_changed"
    assert run(page, "edit_save", **args)["state"] == "editor_changed"
    assert page.evaluate("window.saves || 0") == 0
    assert page.locator(f"#{message_id}").inner_text() == expected
    assert page.locator("#inline-editor").inner_text() == "changed outside this request"
    assert page.locator("#main-composer").inner_text().strip() == ""
