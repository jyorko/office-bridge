"""Read-only Windows Outlook smoke with sanitized evidence and no send test."""

import json
import os
import time
from datetime import datetime, timezone

from _bootstrap import ROOT
from mcp_client import MCPClient, unwrap

from office_bridge.outlook.models import InboxPage, MessageDetail
from office_bridge.outlook.session import invoke


def main():
    control = json.loads((ROOT / ".state/outlook-session-control.json").read_text())
    os.environ["OFFICE_OUTLOOK_PIPE"] = control["pipe"]
    deadline = time.monotonic() + 90
    status = invoke("outlook_status", {}, timeout=30)
    while status.get("state") == "loading" and time.monotonic() < deadline:
        time.sleep(0.5)
        status = invoke("outlook_status", {}, timeout=30)
    facts = {
        key: status.get(key)
        for key in ("state", "account_verified", "host_visible", "host_focused", "generation")
    }
    print(json.dumps(facts), flush=True)
    assert status.get("account_verified") is True, "Outlook account is not verified"
    assert status["host_visible"] is False and status["host_focused"] is False
    with MCPClient() as client:
        tools = {tool["name"]: tool for tool in client.tools()}
        for name in ("outlook_inbox_list", "outlook_get_message", "outlook_search_inbox"):
            assert tools[name]["annotations"]["readOnlyHint"] is True
        page = InboxPage.model_validate(unwrap(client.call("outlook_inbox_list", {"limit": 10})))
        print(f"Outlook listed {len(page.items)} conversations.", flush=True)
        failures = []
        detail = None
        for summary in page.items:
            try:
                result = client.call(
                    "outlook_get_message", {"message": summary.ref.model_dump(mode="json")}
                )
            except RuntimeError:
                failures.append("read_rejected")
                print("Conversation read rejected.", flush=True)
                continue
            if result.get("isError"):
                # Bounded error codes only, never corporate subjects or bodies.
                failures.append("read_rejected")
                continue
            detail = MessageDetail.model_validate(unwrap(result))
            if detail.version.content_complete:
                break
        assert detail is not None, "No exact single-message read succeeded"
        assert detail.ref.entry_id and detail.ref.conversation_id
        evidence = {
            **facts,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "listed_count": len(page.items),
            "scanned_count": page.scanned_count,
            "truncated": page.truncated,
            "read_rejections": len(failures),
            "exact_message_verified": True,
            "content_complete": detail.version.content_complete,
            "body_chars": len(detail.body_text),
            "body_truncated": detail.body_truncated,
        }
        (ROOT / ".state/outlook-verification.json").write_text(json.dumps(evidence, indent=2))
        # Private input for optional, separately run draft verification.
        (ROOT / ".state/outlook-smoke-message.json").write_text(
            detail.model_dump_json(), encoding="utf-8"
        )
        print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
