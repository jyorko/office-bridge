"""Exercise HTTP MCP discovery and desktop reads without changing mailbox items."""

import json
import time

import requests
from _bootstrap import ROOT
from mcp_client import decode

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
    "teams_send_self_message",
}


def main():
    client = requests.Session()
    client.trust_env = False
    client.headers.update(
        {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    )
    for attempt in range(45):
        try:
            denied = client.post(
                URL, json={"jsonrpc": "2.0", "method": "tools/list", "id": 0}, timeout=2
            )
            break
        except requests.ConnectionError:
            if attempt == 44:
                raise SystemExit("Action Server did not become ready within 45 seconds.")
            time.sleep(1)
    assert denied.status_code in (401, 403), "MCP accepted unauthenticated access"
    client.headers["Authorization"] = "Bearer " + (ROOT / ".state/.api_key").read_text().strip()
    response = client.post(
        URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "office-smoke", "version": "1.0"},
            },
        },
        timeout=30,
    )
    initialized = decode(response)
    assert "result" in initialized, "MCP initialization failed"
    if sid := response.headers.get("Mcp-Session-Id"):
        client.headers["Mcp-Session-Id"] = sid
    client.headers["MCP-Protocol-Version"] = initialized["result"]["protocolVersion"]
    ready = client.post(
        URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10
    )
    ready.raise_for_status()
    next_id = 1

    def rpc(method, params=None):
        nonlocal next_id
        next_id += 1
        message = {"jsonrpc": "2.0", "id": next_id, "method": method}
        if params is not None:
            message["params"] = params
        reply = decode(client.post(URL, json=message, timeout=120))
        if "error" in reply:
            raise RuntimeError(f"MCP {method} failed: {reply['error'].get('code')}")
        return reply["result"]

    tools = rpc("tools/list")["tools"]
    names = {}
    for expected in EXPECTED:
        matches = [t["name"] for t in tools if t["name"].endswith(expected)]
        assert len(matches) == 1, f"Missing or ambiguous tool: {expected}"
        names[expected] = matches[0]
    report = {
        "authenticated": True,
        "unauthenticated_status": denied.status_code,
        "protocol": initialized["result"]["protocolVersion"],
        "tools": list(names),
        "checks": [],
    }

    def call(name, args):
        answer = rpc("tools/call", {"name": names[name], "arguments": args})
        (ROOT / f".state/smoke-{name}.json").write_text(
            json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert not answer.get(
            "isError", False
        ), f"{name} returned an action error; inspect private smoke result"
        value = answer.get("structuredContent")
        if value is None:
            chunks = [c["text"] for c in answer.get("content", []) if c.get("type") == "text"]
            value = json.loads("\n".join(chunks))
        if isinstance(value, dict) and "result" in value:
            assert not value.get("error"), f"{name} returned a failure"
            value = value["result"]
        report["checks"].append(name)
        return value

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
                c
                for c in chats["items"]
                if c["ref"]["window_title"].startswith("Chat | " + c["display_name"] + " |")
            ),
            chats["items"][0],
        )
        history = call("teams_read_chat", {"chat": chat["ref"], "limit": 5})
        assert history["scope"] == "currently_loaded_history"
    report["inbox_sample_count"] = len(inbox["items"])
    report["visible_chat_count"] = len(chats["items"])
    report["writes_tested"] = False
    (ROOT / ".state/verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    client.delete(URL, timeout=10)


if __name__ == "__main__":
    main()
