"""Refresh the verified endpoint in Codex and Copilot, preserving other entries."""

import argparse
import csv
import json
import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

import tomlkit
from _bootstrap import ROOT

from office_bridge.runtime.endpoint import server_record

CONFIG = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
COPILOT_CONFIG = (
    Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot") / "mcp-config.json"
)
NAME = "desktop-office"


def replace_file(path: Path, before: bytes | None, content: str) -> None:
    """Replace atomically after an optimistic check; retain no backup copies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".office-bridge-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            if os.name == "nt":
                identity = subprocess.check_output(
                    ["whoami.exe", "/user", "/fo", "csv", "/nh"], text=True
                )
                sid = next(csv.reader([identity.strip()]))[1]
                secured = subprocess.run(
                    [
                        "icacls.exe",
                        str(temporary),
                        "/inheritance:r",
                        "/grant:r",
                        f"*{sid}:F",
                        "*S-1-5-18:F",
                    ],
                    capture_output=True,
                )
                if secured.returncode:
                    raise RuntimeError("Could not restrict MCP configuration access")
            else:
                os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.read_bytes() if path.exists() else None
        if current != before:
            raise RuntimeError(f"Configuration changed concurrently: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def registration_documents(path: Path, *, codex: bool, url: str):
    before = path.read_bytes() if path.exists() else None
    text = (before or b"").decode("utf-8-sig")
    document = tomlkit.parse(text) if codex else json.loads(text if text.strip() else "{}")
    section = "mcp_servers" if codex else "mcpServers"
    if not isinstance(document, (dict, tomlkit.TOMLDocument)):
        raise ValueError(f"Invalid MCP configuration: {path}")
    if section not in document:
        document[section] = {}
    servers = document[section]
    if not hasattr(servers, "items"):
        raise ValueError(f"Invalid MCP server table: {path}")
    servers.pop(NAME, None)
    render = (
        tomlkit.dumps
        if codex
        else lambda value: json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    )
    removed = render(document)
    entry = {"url": url}
    if codex:
        entry.update(startup_timeout_sec=30, tool_timeout_sec=120)
    else:
        entry.update(type="http", tools=["*"], timeout=120000)
    servers[NAME] = entry
    added = render(document)
    if codex and not removed.strip():
        added = added.lstrip("\n")
    return before, removed, added, entry


def register(
    *,
    dev: bool,
    skip_codex: bool | None = None,
    skip_copilot: bool | None = None,
) -> None:
    if skip_codex is None:
        skip_codex = os.environ.get("OFFICE_BRIDGE_SKIP_CODEX") == "1"
    if skip_copilot is None:
        skip_copilot = os.environ.get("OFFICE_BRIDGE_SKIP_COPILOT") == "1"
    record = server_record(ROOT)
    evidence = json.loads((ROOT / ".state/bootstrap-verification.json").read_text())
    if not (
        evidence.get("verified") is True
        and evidence.get("dev") is dev
        and evidence.get("mcp_url") == record["mcp_url"]
        and evidence.get("server_instance") == record["instance"]
        and "teams_web_read_chat" in evidence.get("checks", [])
    ):
        raise SystemExit("Verify this server instance and mode before MCP registration.")
    # Parse both files before removing either existing entry.
    changes = [
        (path, codex, registration_documents(path, codex=codex, url=record["mcp_url"]))
        for path, codex, skip in (
            (CONFIG, True, skip_codex),
            (COPILOT_CONFIG, False, skip_copilot),
        )
        if not skip
    ]
    for path, codex, (before, removed, added, entry) in changes:
        replace_file(path, before, removed)
        section = "mcp_servers" if codex else "mcpServers"
        parse = tomllib.loads if codex else json.loads
        if NAME in parse(path.read_text(encoding="utf-8")).get(section, {}):
            raise RuntimeError("Old Office Bridge MCP entry was not removed")
        replace_file(path, removed.encode("utf-8"), added)
        if parse(path.read_text(encoding="utf-8"))[section].get(NAME) != entry:
            raise RuntimeError("Office Bridge MCP registration read-back failed")
        print(f"Refreshed {'Codex' if codex else 'Copilot'} MCP: {record['mcp_url']} ({path})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument(
        "--skip-codex",
        action="store_true",
        default=os.environ.get("OFFICE_BRIDGE_SKIP_CODEX") == "1",
    )
    parser.add_argument(
        "--skip-copilot",
        action="store_true",
        default=os.environ.get("OFFICE_BRIDGE_SKIP_COPILOT") == "1",
    )
    args = parser.parse_args()
    register(dev=args.dev, skip_codex=args.skip_codex, skip_copilot=args.skip_copilot)


if __name__ == "__main__":
    main()
