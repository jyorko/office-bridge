"""Check a new install through MCP without sending mail or Teams messages."""

import argparse
import json
import time

import requests
from _bootstrap import ROOT
from mcp_client import MCPClient, unwrap

URL = "http://127.0.0.1:8766/mcp"


def wait_for_server(timeout: int) -> None:
    deadline = time.monotonic() + timeout
    with requests.Session() as client:
        client.trust_env = False
        while time.monotonic() < deadline:
            try:
                reply = client.get("http://127.0.0.1:8766/openapi.json", timeout=2)
                if reply.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(1)
    raise RuntimeError("Server did not become ready; inspect .state/server.stderr.log")


def verify(*, dev: bool, timeout: int = 600) -> dict:
    wait_for_server(timeout)
    token = None if dev else (ROOT / ".state/.api_key").read_text().strip()
    with requests.Session() as probe:
        probe.trust_env = False
        reply = probe.post(
            URL, json={"jsonrpc": "2.0", "method": "tools/list", "id": 1}, timeout=10
        )
        denied = reply.status_code in (401, 403)
    if not dev and not denied:
        raise RuntimeError("Authenticated server accepted an unauthenticated MCP request")

    with MCPClient(token) as client:
        tools = client.tools()

        def tool(suffix):
            names = [item["name"] for item in tools if item["name"].endswith(suffix)]
            if len(names) != 1:
                raise RuntimeError(f"Missing or ambiguous action: {suffix}")
            return names[0]

        tool("outlook_inbox_list")
        status_tool = tool("teams_web_status")
        read_tool = tool("teams_web_read_self_chat")
        deadline = time.monotonic() + 120
        while True:
            status = unwrap(client.call(status_tool, {}))
            if status["state"] != "loading" or time.monotonic() >= deadline:
                break
            time.sleep(2)
        background = (
            status.get("backend") == "webview2"
            and status.get("host_visible") is False
            and status.get("control_visible") is False
            and status.get("host_focused") is False
            and status.get("foreground_interference") is False
            and status.get("restart_required") is False
        )
        authenticated = (
            status["state"] == "authenticated" and status.get("account_verified") is True
        )
        report = {
            "verified": False,
            "dev": dev,
            "mcp_url": URL,
            "authenticated": not dev and denied,
            "tool_count": len(tools),
            "teams_state": status["state"],
            "teams_account_verified": status.get("account_verified") is True,
            "background": background,
            "checks": ["mcp_discovery", "teams_web_status"],
            "writes_tested": False,
        }
        destination = ROOT / ".state/bootstrap-verification.json"
        if not authenticated or not background:
            destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
            raise RuntimeError(
                f"Teams bootstrap is not verified ({status['state']}); inspect private status logs"
            )
        history = unwrap(client.call(read_tool, {"limit": 1}))
        if history.get("scope") != "currently_loaded_history":
            raise RuntimeError("Teams self-chat read was not verified")
        report["checks"].append("teams_web_read_self_chat")
        report["self_chat_message_count"] = len(history["messages"])
        report["verified"] = True
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    print("Waiting for the managed Action Server and verifying Teams (read-only)...", flush=True)
    print(json.dumps(verify(dev=args.dev, timeout=args.timeout), indent=2))


if __name__ == "__main__":
    main()
