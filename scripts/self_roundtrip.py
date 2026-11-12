"""Send the user-authorized test messages only to Joshua's own work account."""

import ctypes
import json
import time
import uuid
from datetime import datetime, timezone

import requests
from _bootstrap import ROOT
from mcp_client import decode

URL = "http://127.0.0.1:8766/mcp"
SELF = "joshua.yorko@gainwelltechnologies.com"
AUDIT = ROOT / ".state/self-send-test.json"


def main():
    if AUDIT.exists():
        audit = json.loads(AUDIT.read_text())
        if audit.get("mail") == "received" and audit.get("teams") == "received":
            print(json.dumps(audit, indent=2))
            return
        if audit.get("mail") not in ("todo", "received") or audit.get("teams") not in (
            "todo",
            "received",
        ):
            raise SystemExit(
                "A previous send may have occurred. Inspect the audit before any retry."
            )
    else:
        audit = {
            "run_id": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6],
            "recipient": SELF,
            "mail": "todo",
            "teams": "todo",
        }

    def save():
        AUDIT.write_text(json.dumps(audit, indent=2), encoding="utf-8")

    client = requests.Session()
    client.trust_env = False
    client.headers.update(
        {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (ROOT / ".state/.api_key").read_text().strip(),
        }
    )
    response = client.post(
        URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "authorized-self-send-test", "version": "1.0"},
            },
        },
        timeout=30,
    )
    initialized = decode(response)["result"]
    if sid := response.headers.get("Mcp-Session-Id"):
        client.headers["Mcp-Session-Id"] = sid
    client.headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
    client.post(
        URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10
    ).raise_for_status()
    request_id = 1

    def rpc(method, params=None):
        nonlocal request_id
        request_id += 1
        data = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            data["params"] = params
        answer = decode(client.post(URL, json=data, timeout=120))
        if "error" in answer:
            raise RuntimeError(f"MCP error in {method}")
        return answer["result"]

    tools = rpc("tools/list")["tools"]

    def call(name, arguments):
        matches = [t["name"] for t in tools if t["name"].endswith(name)]
        assert len(matches) == 1, f"Tool unavailable: {name}"
        result = rpc("tools/call", {"name": matches[0], "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"{name} failed")
        value = result.get("structuredContent")
        if value is None:
            value = json.loads(
                "\n".join(c["text"] for c in result.get("content", []) if c.get("type") == "text")
            )
        if "error" in value and value["error"]:
            raise RuntimeError(f"{name}: {value['error']}")
        return value.get("result", value)

    audit["email_read_count"] = len(call("outlook_inbox_list", {"limit": 3})["items"])
    subject = "Local Action Server test " + audit["run_id"]
    if audit["mail"] == "todo":
        save()
        audit["mail"] = "creating_draft"
        save()
        draft = call(
            "outlook_create_mail_draft",
            {
                "recipient_email": SELF,
                "subject": subject,
                "body_text": "Hey Josh, this is the Outlook test you asked for. Sending it only to you to check the new local setup.\n\nTest: "
                + audit["run_id"],
            },
        )
        assert draft["recipients"] and all(
            r["smtp_address"].lower() == SELF for r in draft["recipients"]
        ), "Unexpected mail recipient"
        assert draft["sending_account"].lower() == SELF, "Unexpected sending account"
        audit["draft_ref"] = draft["ref"]
        audit["mail"] = "send_started"
        save()
        receipt = call(
            "outlook_send_draft", {"draft": draft["ref"], "expected_digest": draft["digest"]}
        )
        assert receipt["status"] == "submitted"
        audit["mail"] = "submitted"
        save()
        for attempt in range(6):
            messages = call(
                "outlook_search_inbox", {"query": {"subject_contains": subject}, "limit": 5}
            )["items"]
            exact = [m for m in messages if m["subject"] == subject]
            if exact:
                detail = call("outlook_get_message", {"message": exact[0]["ref"]})
                assert audit["run_id"] in detail["body_text"]
                audit["mail"] = "received"
                audit["received_mail_ref"] = detail["ref"]
                save()
                break
            time.sleep(2)
        assert (
            audit["mail"] == "received"
        ), "Mail submitted; delivery not yet verified. Do not send again."
    if audit["teams"] == "todo":
        chats = call("teams_list_chats", {"limit": 50})["items"]
        self_chats = [c for c in chats if "Yorko, Joshua (You)" in c["display_name"]]
        assert len(self_chats) == 1, "Expected one recognized self-chat"
        prior = call("teams_read_chat", {"chat": self_chats[0]["ref"], "limit": 3})
        audit["teams_read_count"] = len(prior["messages"])
        audit["teams"] = "send_started"
        save()
        foreground_before = ctypes.windll.user32.GetForegroundWindow()
        call(
            "teams_send_self_message",
            {
                "body_text": "Hey Josh, here's the Teams test you asked for. This went only to your self-chat. Test: "
                + audit["run_id"]
            },
        )
        audit["teams_foreground_unchanged"] = (
            foreground_before == ctypes.windll.user32.GetForegroundWindow()
        )
        chats = call("teams_list_chats", {"limit": 50})["items"]
        self_chat = next(c for c in chats if "Yorko, Joshua (You)" in c["display_name"])
        after = call("teams_read_chat", {"chat": self_chat["ref"], "limit": 10})
        assert any(
            audit["run_id"] in m["text"] for m in after["messages"]
        ), "Teams send outcome requires inspection; do not retry."
        audit["teams"] = "received"
        save()
    print(json.dumps({k: v for k, v in audit.items() if not k.endswith("_ref")}, indent=2))
    client.delete(URL, timeout=10)


if __name__ == "__main__":
    main()
