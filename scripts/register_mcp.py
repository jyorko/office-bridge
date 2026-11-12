"""Register the verified local server in Codex while preserving unrelated settings."""

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

from _bootstrap import ROOT

CONFIG = Path.home() / ".codex/config.toml"
NAME = "desktop-office"


def main():
    evidence = json.loads((ROOT / ".state/verification.json").read_text())
    if len(evidence["checks"]) != 5 or not evidence["authenticated"]:
        raise SystemExit("Complete the live read verification before registration.")
    key = (ROOT / ".state/.api_key").read_text().strip()
    before = CONFIG.read_bytes() if CONFIG.exists() else b""
    existing = tomllib.loads(before.decode("utf-8-sig"))
    server = existing.get("mcp_servers", {}).get(NAME)
    if server is not None:
        if (
            server.get("url") == "http://127.0.0.1:8766/mcp"
            and server.get("http_headers", {}).get("Authorization") == "Bearer " + key
        ):
            print("desktop-office MCP is already registered.")
            return
        raise SystemExit(
            "An existing desktop-office configuration differs; preserve it for inspection."
        )
    backup = ROOT / ".state/config.toml.before-office"
    if CONFIG.exists() and not backup.exists():
        shutil.copyfile(CONFIG, backup)
    addition = (
        '\n\n[mcp_servers.desktop-office]\n'
        'url = "http://127.0.0.1:8766/mcp"\n'
        'startup_timeout_sec = 30\n'
        'tool_timeout_sec = 90\n'
        '[mcp_servers.desktop-office.http_headers]\n'
        f'Authorization = {json.dumps("Bearer " + key)}\n'
    )
    updated = before.decode("utf-8-sig") + addition
    tomllib.loads(updated)
    temporary = CONFIG.with_name("config.office.tmp")
    temporary.write_text(updated, encoding="utf-8")
    identity = subprocess.check_output(["whoami.exe"], text=True).strip()
    secured = subprocess.run(
        [
            "icacls.exe",
            str(temporary),
            "/inheritance:r",
            "/grant:r",
            identity + ":F",
            "*S-1-5-18:F",
        ],
        capture_output=True,
    )
    if secured.returncode:
        temporary.unlink()
        raise SystemExit("Could not restrict configuration file access.")
    if CONFIG.exists() and CONFIG.read_bytes() != before:
        temporary.unlink()
        raise SystemExit("Configuration changed concurrently; retry registration.")
    os.replace(temporary, CONFIG)
    print("Registered desktop-office: http://127.0.0.1:8766/mcp (authenticated).")


if __name__ == "__main__":
    main()
