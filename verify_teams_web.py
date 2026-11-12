"""Verify Teams status through fresh managed MCP action workers; never sends."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import requests

from verify_mcp import decode

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8766/mcp"


def check_status(dev: bool, backend: str | None = None) -> dict:
    with requests.Session() as client:
        client.trust_env = False
        client.headers.update({"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
        if not dev:
            client.headers["Authorization"] = "Bearer " + (ROOT / ".state/.api_key").read_text().strip()
        response = client.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "teams-headless-verification", "version": "1.0"},
        }}, timeout=30)
        initialized = decode(response)["result"]
        if session_id := response.headers.get("Mcp-Session-Id"):
            client.headers["Mcp-Session-Id"] = session_id
        client.headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
        client.post(URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10).raise_for_status()

        def rpc(request_id, method, params=None):
            message = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            reply = decode(client.post(URL, json=message, timeout=120))
            if "error" in reply:
                raise RuntimeError(f"MCP {method} failed: {reply['error'].get('code')}")
            return reply["result"]

        try:
            tools = rpc(2, "tools/list")["tools"]
            names = [tool["name"] for tool in tools if tool["name"].endswith("teams_web_status")]
            if len(names) != 1:
                raise RuntimeError("Expected exactly one Teams web status tool")
            foreground = ctypes.windll.user32.GetForegroundWindow()
            arguments = {"browser_engine": backend} if backend else {}
            result = rpc(3, "tools/call", {"name": names[0], "arguments": arguments})
            unchanged = foreground == ctypes.windll.user32.GetForegroundWindow()
            if result.get("isError"):
                raise RuntimeError("Teams status action failed")
            value = result.get("structuredContent")
            if value is None:
                value = json.loads("\n".join(item["text"] for item in result.get("content", []) if item.get("type") == "text"))
            if value.get("error"):
                raise RuntimeError("Teams status returned an action error")
            return {"status": value.get("result", value), "foreground_unchanged": unchanged}
        finally:
            client.delete(URL, timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Use the local development endpoint without credentials.")
    parser.add_argument("--count", type=int, choices=range(1, 11), default=2)
    parser.add_argument("--backend", choices=("webview2", "chromium", "msedge"))
    parser.add_argument("--expected-state", choices=("authenticated", "needs_interactive_login", "access_blocked", "loading", "failed"), default="authenticated")
    args = parser.parse_args()
    report = {"time": datetime.now(timezone.utc).isoformat(), "expected_state": args.expected_state, "calls": []}
    destination = ROOT / ".state/teams-web-verification.json"
    for _ in range(args.count):
        started = time.monotonic()
        call = check_status(args.dev, args.backend)
        call["round_trip_seconds"] = round(time.monotonic() - started, 3)
        report["calls"].append(call)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    def passed_call(call):
        status = call["status"]
        if status["state"] != args.expected_state:
            return False
        if status.get("backend") == "webview2":
            background = (status.get("foreground_interference") is False and status.get("host_focused") is False and status.get("restart_required") is False
                          and status.get("host_visible") is False and status.get("control_visible") is False)
        else:
            background = call["foreground_unchanged"]
        identity = args.expected_state != "authenticated" or status.get("account_verified") is True
        return background and identity

    generations = [call["status"].get("generation") for call in report["calls"] if call["status"].get("backend") == "webview2"]
    same_session = not generations or (all(generations) and len(set(generations)) == 1)
    report["same_session"] = same_session
    passed = all(passed_call(call) for call in report["calls"]) and same_session
    report["verdict"] = "VERIFIED" if passed else "NOT VERIFIED"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
