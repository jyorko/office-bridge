"""Exercise HTTP MCP discovery and desktop reads without changing mailbox items."""

import json
import time

import requests
from _bootstrap import ROOT
from mcp_client import MCPClient, unwrap

URL = "http://127.0.0.1:8766/mcp"
EXPECTED = {
    "outlook_inbox_list",
    "outlook_get_message",
    "outlook_search_inbox",
    "outlook_create_reply_draft",
    "outlook_send_draft",
    "outlook_archive",
    "teams_list_chats",
    "teams_read_chat",
    "outlook_create_mail_draft",
}


def main():
    probe = requests.Session()
    probe.trust_env = False
    probe.headers.pop("Authorization", None)
    probe.headers.update(
        {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    )
    assert "Authorization" not in probe.headers
    for attempt in range(45):
        try:
            denied = probe.post(
                URL, json={"jsonrpc": "2.0", "method": "tools/list", "id": 0}, timeout=2
            )
            break
        except requests.ConnectionError:
            if attempt == 44:
                raise SystemExit("Action Server did not become ready within 45 seconds.")
            time.sleep(1)
    assert denied.status_code in (401, 403), "MCP accepted unauthenticated access"
    probe.close()

    token = (ROOT / ".state/.api_key").read_text().strip()
    with MCPClient(token) as client:
        tools = client.tools()
        names = {}
        for expected in EXPECTED:
            matches = [tool["name"] for tool in tools if tool["name"].endswith(expected)]
            assert len(matches) == 1, f"Missing or ambiguous tool: {expected}"
            names[expected] = matches[0]
        report = {
            "authenticated": True,
            "unauthenticated_status": denied.status_code,
            "protocol": client.protocol,
            "tools": list(names),
            "checks": [],
        }

        def call(name, args):
            answer = client.call(names[name], args)
            (ROOT / f".state/smoke-{name}.json").write_text(
                json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            report["checks"].append(name)
            return unwrap(answer)

        inbox = call("outlook_inbox_list", {"limit": 3, "unread_only": False})
        assert isinstance(inbox.get("items"), list)
        if inbox["items"]:
            detail = call("outlook_get_message", {"message": inbox["items"][0]["ref"]})
            assert "body_text" in detail
        call("outlook_search_inbox", {"query": {"subject_contains": "Verint"}, "limit": 3})
        chats = call("teams_list_chats", {"limit": 50})
        assert isinstance(chats.get("items"), list)
        if chats["items"]:
            chat = next(
                (
                    item
                    for item in chats["items"]
                    if item["ref"]["window_title"].startswith(
                        "Chat | " + item["display_name"] + " |"
                    )
                ),
                chats[0],
            )
            history = call("teams_read_chat", {"chat": chat["ref"], "limit": 5})
            assert history["scope"] == "currently_loaded_history"
        report["inbox_sample_count"] = len(inbox["items"])
        report["visible_chat_count"] = len(chats["items"])
        report["writes_tested"] = False
        (ROOT / ".state/verification.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
