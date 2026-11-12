"""Verify Teams status through fresh managed MCP action workers; never sends."""

import argparse
import ctypes
import json
import time
from datetime import datetime, timezone

from _bootstrap import ROOT
from mcp_client import MCPClient, unwrap


def check_status(dev: bool, backend: str | None = None) -> dict:
    token = None if dev else (ROOT / ".state/.api_key").read_text().strip()
    with MCPClient(token) as client:
        names = [
            tool["name"] for tool in client.tools() if tool["name"].endswith("teams_web_status")
        ]
        if len(names) != 1:
            raise RuntimeError("Expected exactly one Teams web status tool")
        foreground = ctypes.windll.user32.GetForegroundWindow()
        arguments = {"browser_engine": backend} if backend else {}
        result = client.call(names[0], arguments)
        unchanged = foreground == ctypes.windll.user32.GetForegroundWindow()
        return {"status": unwrap(result), "foreground_unchanged": unchanged}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dev", action="store_true", help="Use the local development endpoint without credentials."
    )
    parser.add_argument("--count", type=int, choices=range(1, 11), default=2)
    parser.add_argument("--backend", choices=("webview2", "chromium", "msedge"))
    parser.add_argument(
        "--expected-state",
        choices=("authenticated", "needs_interactive_login", "access_blocked", "loading", "failed"),
        default="authenticated",
    )
    args = parser.parse_args()
    report = {
        "time": datetime.now(timezone.utc).isoformat(),
        "expected_state": args.expected_state,
        "calls": [],
    }
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
            background = (
                status.get("foreground_interference") is False
                and status.get("host_focused") is False
                and status.get("restart_required") is False
                and status.get("host_visible") is False
                and status.get("control_visible") is False
            )
        else:
            background = call["foreground_unchanged"]
        identity = args.expected_state != "authenticated" or status.get("account_verified") is True
        return background and identity

    generations = [
        call["status"].get("generation")
        for call in report["calls"]
        if call["status"].get("backend") == "webview2"
    ]
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
