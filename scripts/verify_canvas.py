"""Verify the published Canvas catalog, signed-in read path and script-free UI parity."""

import concurrent.futures
import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from mcp_client import MCPClient, unwrap
from playwright.sync_api import sync_playwright

from office_bridge.canvas_window import start_viewer
from office_bridge.presentation import Presentation, app_html, message_view, present

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
    source.postMessage({jsonrpc:'2.0',id:data.id,result:{structuredContent:{opened:true}}}, '*');
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
            "outlook_send_draft",
            "teams_read_chat",
            "teams_web_read_chat",
            "teams_web_send_chat",
        ):
            assert tools[name]["_meta"]["ui"]["resourceUri"] == uri
        assert tools["teams_web_read_chat"]["annotations"]["readOnlyHint"]
        assert not tools["teams_web_send_chat"]["annotations"]["readOnlyHint"]
        assert "app" in tools["office_open_message_view"]["_meta"]["ui"]["visibility"]
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
        browser.close()


def check_viewer(display):
    # Independent, short-lived callers exercise the production launcher. The
    # verification switch suppresses Show() only; WebView2 still loads the DOM.
    def caller():
        run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--viewer-worker"],
            input=json.dumps(display),
            text=True,
            capture_output=True,
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
        print(
            "Verified live MCP resource, hidden Teams self-chat, five native/standalone views, keyboard interaction, receipt fallbacks and concurrent hidden WebView2 reuse across worker exits."
        )
