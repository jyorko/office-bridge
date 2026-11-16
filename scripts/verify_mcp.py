"""Exercise HTTP MCP discovery and desktop reads without changing mailbox items."""

import json

from _bootstrap import ROOT
from bootstrap_check import wait_for_server
from mcp_client import MCPClient, unwrap

from office_bridge.runtime.endpoint import mcp_url

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
    url = mcp_url(ROOT)
    wait_for_server(600)
    with MCPClient() as client:
        tools = client.tools()
        names = {}
        for expected in EXPECTED:
            matches = [tool["name"] for tool in tools if tool["name"].endswith(expected)]
            assert len(matches) == 1, f"Missing or ambiguous tool: {expected}"
            names[expected] = matches[0]
        report = {
            "mcp_url": url,
            "api_key_required": False,
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
