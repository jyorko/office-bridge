"""Verify the published Canvas catalog, local UI and non-repeating mail controls."""

import concurrent.futures
import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from actions import Response
from mcp_client import MCPClient, unwrap
from playwright.sync_api import sync_playwright

import outlook_actions
from office_bridge.canvas_window import start_viewer
from office_bridge.outlook.models import InboxPage, MessageDetail, MessageVersion, ReplyDraft
from office_bridge.presentation import Presentation, ReadMail, app_html, message_view, present

HOST = """<iframe sandbox="allow-scripts" title="Office" style="width:100%;height:900px;border:0"></iframe>
<script>
const frame = document.querySelector('iframe');
window.calls = [];
window.deliver = params => frame.contentWindow.postMessage({jsonrpc:'2.0', method:'ui/notifications/tool-result', params}, '*');
window.addEventListener('message', ({source, data}) => {
  if (source !== frame.contentWindow || data?.jsonrpc !== '2.0') return;
  if (data.method === 'ui/initialize') source.postMessage({jsonrpc:'2.0', id:data.id, result:{
    protocolVersion:data.params.protocolVersion, hostCapabilities:{serverTools:{}}, hostContext:{theme:'dark'},
    hostInfo:{name:'Synthetic host', version:'1'}
  }}, '*');
  if (data.method === 'ui/notifications/initialized') window.initialized = true;
  if (data.method === 'tools/call') {
    window.calls.push(data.params);
    const result = window.nextResult || {structuredContent:{opened:true,view_id:data.params.arguments.view_id}};
    if (!window.hold) source.postMessage({jsonrpc:'2.0',id:data.id,result}, '*');
  }
});
</script>"""


def load_results():
    with MCPClient() as client:
        tools = {tool["name"]: tool for tool in client.tools()}
        uri = tools["office_present_messages"]["_meta"]["ui"]["resourceUri"]
        for name in (
            "outlook_inbox_list",
            "outlook_search_inbox",
            "outlook_get_message",
            "outlook_create_reply_draft",
            "outlook_send_draft",
            "teams_read_chat",
            "teams_web_read_chat",
            "teams_web_send_chat",
        ):
            assert tools[name]["_meta"]["ui"]["resourceUri"] == uri
        assert tools["teams_web_read_chat"]["annotations"]["readOnlyHint"]
        assert not tools["teams_web_send_chat"]["annotations"]["readOnlyHint"]
        assert "app" in tools["office_open_message_view"]["_meta"]["ui"]["visibility"]
        for name, required in (
            ("outlook_get_message", {"message"}),
            ("outlook_create_reply_draft", {"source", "expected_source", "body_text"}),
        ):
            assert "app" in tools[name]["_meta"]["ui"]["visibility"]
            assert set(tools[name]["inputSchema"]["required"]) == required
        assert "app" not in tools["outlook_send_draft"]["_meta"]["ui"]["visibility"]
        resource = client.rpc("resources/read", {"uri": uri})["contents"][0]
        assert (
            resource["mimeType"] == "text/html;profile=mcp-app" and resource["text"] == app_html()
        )
        views = [
            message_view(result)
            for result in (
                {
                    "items": [
                        {
                            "subject": "Field notes",
                            "sender": {
                                "display_name": "Mira",
                                "smtp_address": "mira@example.invalid",
                            },
                            "received_at": "2026-11-18T14:30:00Z",
                            "unread": True,
                            "flagged": True,
                        },
                        {
                            "subject": "Updated schedule",
                            "sender": {"display_name": "Alex"},
                            "received_at": "09:15",
                            "unread": False,
                            "importance": "normal",
                        },
                    ],
                    "scanned_count": 2,
                    "truncated": True,
                },
                {
                    "chat": {"title": "Design review"},
                    "messages": [
                        {"author": "Mira", "time": "09:15", "text": "<img src=x> & notes\n" * 30}
                    ],
                },
                {
                    "status": "submitted",
                    "draft": {},
                    "subject": "Thursday review",
                    "submitted_at": "2026-11-18T14:30:00Z",
                    "body_text": "Receipt text\n" * 50,
                    "body_truncated": True,
                },
                {
                    "sender": {"display_name": "Inez"},
                    "subject": "Proofs",
                    "received_at": "10:00",
                    "body_text": "Full email\n" * 75,
                },
                {"items": [], "scanned_count": 500, "truncated": True},
            )
        ]
        displays = [
            unwrap(client.call("office_present_messages", {"view": view.model_dump()}))
            for view in views
        ]
        status = unwrap(client.call("teams_web_status", {}))
        assert (
            status["account_verified"]
            and status["backend"] == "webview2"
            and status["host_visible"] is status["host_focused"] is False
        )
        chats = unwrap(client.call("teams_web_list_chats", {"limit": 100}))
        (notes,) = [item for item in chats["items"] if item["chat_id"] == "48:notes"]
        chat = {key: notes[key] for key in ("chat_id", "title")}
        history = client.call("teams_web_read_chat", {"chat": chat, "limit": 1})
        result = unwrap(history)
        assert result["chat"] == chat
        assert (
            history["structuredContent"]["presentation"]["view"]
            == message_view(result).model_dump()
        )
        return resource["text"], displays


def mail_fixtures():
    """Generate sanitized responses through the real actions with a fixture-only desktop."""
    subject = "Workshop agenda"
    ref = {"entry_id": "fixture-workshop", "store_id": "fixture-store"}
    version = dict(
        modified_at="2026-11-17T12:00:00Z",
        subject=subject,
        unread=False,
        flagged=False,
        folder_entry_id="fixture-folder",
        importance="normal",
    )
    if "content_complete" in MessageVersion.model_fields:
        version.update(content_complete=True, version_digest="b" * 64)
    item = dict(
        ref=ref,
        version=version,
        sender=dict(display_name="Alex", smtp_address=None),
        subject=subject,
        received_at=version["modified_at"],
        unread=False,
        flagged=False,
        importance="normal",
    )
    other = {
        **item,
        "ref": {**ref, "entry_id": "fixture-other"},
        "subject": "Field notes",
        "unread": True,
    }
    page = InboxPage(items=[other, item], truncated=False, scanned_count=2)
    detail = MessageDetail(
        **{**item, "version": {**version, "modified_at": "2026-11-18T09:00:00Z"}},
        recipients=[],
        body_text="Please review the workshop agenda.",
        body_truncated=False,
    )
    body = "I can review the agenda."
    draft = ReplyDraft(
        ref={**ref, "entry_id": "fixture-draft"},
        source=ref,
        recipients=[{"display_name": "Alex", "smtp_address": "alex@example.invalid"}],
        subject="Re: " + subject,
        body_text=body,
        sending_account="demo@example.invalid",
        digest="a" * 64,
    )
    with patch.object(
        outlook_actions.desktop,
        "run",
        side_effect=[Response(result=value) for value in (page, detail, draft)],
    ):
        results = [
            outlook_actions.outlook_inbox_list(),
            outlook_actions.outlook_get_message(detail.ref),
            outlook_actions.outlook_create_reply_draft(
                detail.ref, body, detail.version, reply_all=True
            ),
        ]
    return [{"structuredContent": value.model_dump(mode="json")} for value in results]


def check_mail_ui(page, app, deliver, respond, calls, *, outcome):
    inbox, read, saved = mail_fixtures()
    deliver(inbox)
    checkbox = app.get_by_role("checkbox")
    checkbox.press("Space")
    checkbox.press("Space")
    app.locator("summary").last.press("Enter")
    assert not calls()
    respond(read)
    app.get_by_role("button", name="Read email").last.press("Enter")
    reply = app.get_by_role("button", name="Reply", exact=True)
    reply.wait_for()
    ref = read["structuredContent"]["result"]["ref"]
    assert calls() == [{"name": "outlook_get_message", "arguments": {"message": ref}}]
    reply.press("Space")
    app.get_by_role("button", name="Cancel").click()
    app.get_by_role("button", name="Reply all", exact=True).press("Space")
    text = app.get_by_role("textbox", name="Reply all")
    create = app.get_by_role("button", name="Create draft")
    assert create.is_disabled()
    body = saved["structuredContent"]["result"]["body_text"]
    text.fill(body)
    deliver(inbox)
    assert text.input_value() == body and len(calls()) == 1
    if outcome == "timeout":
        page.clock.install()
    outcomes = {
        "timeout": None,
        "error": {"isError": True, "content": [{"type": "text", "text": "Source changed."}]},
        "saved": saved,
    }
    respond(outcomes[outcome])
    create.press("Enter")
    if outcome == "timeout":
        page.wait_for_function("window.calls.length === 2")
        page.clock.fast_forward(91000)
    if outcome != "saved":
        app.get_by_text("Check Outlook Drafts", exact=False).wait_for()
        deliver(read)
        assert text.input_value() == body and create.is_disabled()
        create.evaluate("(el)=>el.click()")
    else:
        app.get_by_role("heading", name="Reply draft created").wait_for()
        assert (
            app.get_by_text(body).is_visible()
            and app.get_by_text("alex@example.invalid").is_visible()
        )
        assert not app.get_by_role("button", name="Create draft").count()
    assert calls()[1:] == [
        {
            "name": "outlook_create_reply_draft",
            "arguments": {
                "source": ref,
                "expected_source": read["structuredContent"]["result"]["version"],
                "reply_all": True,
                "body_text": body,
            },
        }
    ]
    assert "fixture-" not in app.locator("body").inner_text()


def check_conversation_read(page, app):
    inbox, opened, _ = mail_fixtures()
    listing = inbox["structuredContent"]["presentation"]
    source = opened["structuredContent"]
    selected = {
        "entry_id": None,
        "conversation_id": "fixture-conversation",
        "store_id": "fixture-store",
    }
    resolved = {**selected, "entry_id": "fixture-workshop"}
    version = {**source["result"]["version"], "content_complete": True, "version_digest": "c" * 64}
    listing["mail"]["rows"] = [None, selected]
    source["result"].update(ref=resolved, version=version)
    binding = source["presentation"]["mail"] = {"source": resolved, "version": version}
    page.evaluate("window.calls=[]")
    page.evaluate("window.deliver", inbox)
    read = app.get_by_role("button", name="Read email")
    read.first.wait_for()
    assert read.count() == 1
    for incorrect in (
        {**resolved, "store_id": "other"},
        {**resolved, "conversation_id": "other"},
        selected,
    ):
        source["result"]["ref"] = incorrect
        page.evaluate("r=>window.nextResult=r", opened)
        read.click()
        app.get_by_text("Outlook did not return the selected email.").wait_for()
        assert not app.get_by_role("button", name="Reply", exact=True).count()
    source["result"]["ref"] = resolved
    page.evaluate("r=>window.nextResult=r", opened)
    read.click()
    app.get_by_role("button", name="Reply", exact=True).wait_for()
    assert all(
        call == {"name": "outlook_get_message", "arguments": {"message": selected}}
        for call in page.evaluate("window.calls")
    )
    binding["version"]["content_complete"] = False
    page.evaluate("window.deliver", opened)
    app.get_by_role("button", name="Reply", exact=True).wait_for(state="hidden")
    assert not app.get_by_role("button", name="Reply", exact=True).count()


def check_ui(resource, displays):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(HOST)
        page.locator("iframe").evaluate("(frame, html) => frame.srcdoc = html", resource)
        page.wait_for_function("window.initialized")
        app = page.frame_locator("iframe")
        plain = browser.new_page(java_script_enabled=False, viewport={"width": 360, "height": 800})
        for index, display in enumerate(displays):
            assert display["html"] == present(Presentation.model_validate(display).view).html
            result = {"structuredContent": {"presentation": display}}
            page.evaluate("window.deliver", result)
            app.get_by_role("heading", name=display["view"]["title"], exact=True).first.wait_for()
            plain.set_content(display["html"])
            assert app.locator("main").inner_text() == plain.locator("main").inner_text()
            assert plain.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            for host in (app, plain):
                if index == 0:
                    first, second = host.locator("summary").all()
                    first.press("Enter")
                    assert host.get_by_text("mira@example.invalid").is_visible()
                    second.press("Enter")
                    assert not host.get_by_text("mira@example.invalid").is_visible()
                    checkbox = host.get_by_role("checkbox", name="Unread only", exact=False)
                    checkbox.press("Space")
                    assert host.get_by_text("Updated schedule").is_hidden()
                    assert host.get_by_text("Field notes").is_visible()
                    checkbox.press("Space")
                elif index in (1, 2):
                    summary = host.get_by_text("Full message", exact=True)
                    assert (
                        host.locator(".preview").is_visible()
                        and not host.locator(".body").is_visible()
                    )
                    summary.press("Enter")
                    assert (
                        host.locator(".body").text_content() == display["view"]["cards"][0]["body"]
                    )
                    assert not host.locator(".preview").is_visible()
                    summary.press("Enter")
                elif index == 3:
                    assert (
                        host.locator(".body").is_visible() and not host.locator("summary").count()
                    )
            assert not page.evaluate("window.calls")
        app.get_by_role("button", name="Open in window").click()
        assert page.evaluate("window.calls") == [
            {
                "name": "office_open_message_view",
                "arguments": {"view_id": display["view_id"], "view": display["view"]},
            }
        ]
        for params, title in (
            (
                {
                    "structuredContent": {
                        "result": {
                            "status": "already_observed",
                            "chat": {"title": "Review"},
                            "time": "09:15",
                        }
                    }
                },
                "Previously observed in Teams",
            ),
            (
                {"structuredContent": {"result": {"status": "submitted", "subject": "Review"}}},
                "Submitted to Outlook",
            ),
            (
                {"structuredContent": {"result": {"status": "already_submitted", "draft": {}}}},
                "Previously submitted to Outlook",
            ),
            ({"structuredContent": {"result": {"status": "outcome_unknown"}}}, "Office result"),
            (
                {
                    "structuredContent": {
                        "result": {"status": "submitted"},
                        "error": "Uncertain outcome",
                    }
                },
                "Office action needs attention",
            ),
        ):
            page.evaluate("window.deliver", params)
            app.get_by_role("heading", name=title, exact=True).wait_for()
            assert not app.get_by_role("button").count()
        check_conversation_read(page, app)
        for outcome in ("saved", "error", "timeout"):
            mail_page = browser.new_page(viewport={"width": 360, "height": 800})
            mail_page.set_content(HOST)
            mail_page.locator("iframe").evaluate("(frame, html) => frame.srcdoc = html", resource)
            mail_page.wait_for_function("window.initialized")
            mail_app = mail_page.frame_locator("iframe")
            check_mail_ui(
                mail_page,
                mail_app,
                lambda result: mail_page.evaluate("window.deliver", result),
                lambda result: mail_page.evaluate(
                    "result=>{window.hold=!result;window.nextResult=result}", result
                ),
                lambda: mail_page.evaluate("window.calls"),
                outcome=outcome,
            )
            mail_page.close()
        browser.close()


def check_viewer(display, *, verify_click=False):
    # Independent, short-lived callers exercise the production launcher. The
    # verification switch suppresses Show() only; WebView2 still loads the DOM.
    def caller():
        run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--viewer-worker"],
            input=json.dumps(display),
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "OFFICE_CANVAS_VERIFY_CLICK": "1" if verify_click else "0",
            },
            timeout=60,
            check=True,
        )
        return tuple(json.loads(run.stdout))

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    handles = set()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(caller) for _ in range(2)]
            for future in concurrent.futures.as_completed(futures):
                handles.add(future.result())
        assert len(handles) == 1
        ((pid, hwnd, count),) = handles
        assert (
            count == len(display["view"]["cards"])
            and user32.IsWindow(hwnd)
            and not user32.IsWindowVisible(hwnd)
        )
        assert caller() == (pid, hwnd, count)
        changed = Presentation.model_validate(display)
        changed.view.title = "Different result"
        try:
            start_viewer(changed, verify_hidden=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Window accepted a different result for its active ID")
    finally:
        for _, hwnd, _ in handles:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
            deadline = time.monotonic() + 5
            while user32.IsWindow(hwnd) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not user32.IsWindow(hwnd)


if __name__ == "__main__":
    if "--viewer-worker" in sys.argv:
        print(
            json.dumps(
                start_viewer(Presentation.model_validate_json(sys.stdin.read()), verify_hidden=True)
            )
        )
    else:
        resource, displays = load_results()
        check_ui(resource, displays)
        check_viewer(displays[0])
        os.environ["OFFICE_CANVAS_SIGNING_KEY"] = secrets.token_hex(32)
        with tempfile.TemporaryDirectory() as temporary, socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            root = Path(temporary)
            (root / ".state").mkdir()
            (root / ".state/server.json").write_text(
                json.dumps(
                    {
                        "instance": "fixture",
                        "mcp_url": f"http://127.0.0.1:{reserved.getsockname()[1]}/mcp",
                    }
                ),
                encoding="utf-8",
            )
            os.environ["OFFICE_CANVAS_ROOT"] = str(root)
            check_viewer(mail_fixtures()[0]["structuredContent"]["presentation"], verify_click=True)
        empty = present(message_view({"items": [], "truncated": False, "scanned_count": 0}))
        empty.mail = ReadMail(rows=[])
        empty.window_proof = empty.proof()
        os.environ["OFFICE_CANVAS_ROOT"] = str(Path(__file__).resolve().parents[1])
        check_viewer(empty.model_dump(mode="json"))
        print(
            "Verified published app metadata and schemas, hidden Teams read, five reading views, keyboard and mobile mail actions, draft timeout without replay, and static and actionable hidden WebView2 window reuse."
        )
