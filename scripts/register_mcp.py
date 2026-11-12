"""Register the verified local server in Codex while preserving unrelated settings."""

import argparse
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

from _bootstrap import ROOT

CONFIG = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
NAME = "desktop-office"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    bootstrap_evidence = ROOT / ".state/bootstrap-verification.json"
    if bootstrap_evidence.exists():
        evidence = json.loads(bootstrap_evidence.read_text())
        if not (
            evidence.get("verified") is True
            and evidence.get("dev") is args.dev
            and evidence.get("mcp_url") == "http://127.0.0.1:8766/mcp"
            and "teams_web_read_chat" in evidence.get("checks", [])
            and (args.dev or evidence.get("authenticated") is True)
        ):
            raise SystemExit(
                "Complete bootstrap_check.py for this server mode before registration."
            )
    else:
        evidence = json.loads((ROOT / ".state/verification.json").read_text())
        if args.dev or len(evidence["checks"]) != 5 or not evidence["authenticated"]:
            raise SystemExit("Complete the live read verification before registration.")
    key = None if args.dev else (ROOT / ".state/.api_key").read_text().strip()
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    before = CONFIG.read_bytes() if CONFIG.exists() else b""
    existing = tomllib.loads(before.decode("utf-8-sig"))
    server = existing.get("mcp_servers", {}).get(NAME)
    if server is not None:
        if server.get("url") == "http://127.0.0.1:8766/mcp" and (
            args.dev or server.get("http_headers", {}).get("Authorization") == "Bearer " + key
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
        "\n\n[mcp_servers.desktop-office]\n"
        'url = "http://127.0.0.1:8766/mcp"\n'
        "startup_timeout_sec = 30\n"
        "tool_timeout_sec = 90\n"
    )
    if key is not None:
        addition += (
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
    mode = "development, loopback without an API key" if args.dev else "authenticated"
    print(f"Registered desktop-office: http://127.0.0.1:8766/mcp ({mode}).")


if __name__ == "__main__":
    main()
