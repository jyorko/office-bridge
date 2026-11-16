"""Resolve the launcher-recorded loopback endpoint, never a guessed port."""

import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PORT = 8766


def startup_port(preferred: int = DEFAULT_PORT) -> int:
    """Prefer the requested port; let Runtime bind port zero when it is unavailable."""
    if not 0 <= preferred <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    if preferred == 0:
        return 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", preferred))
        except OSError:
            return 0
    return preferred


def server_record(root: Path) -> dict:
    try:
        record = json.loads((root / ".state/server.json").read_text(encoding="utf-8-sig"))
        parsed = urlsplit(record["mcp_url"])
        valid = (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and 1 <= parsed.port <= 65535
            and parsed.netloc == f"127.0.0.1:{parsed.port}"
            and parsed.path == "/mcp"
            and not parsed.query
            and not parsed.fragment
            and isinstance(record["instance"], str)
            and bool(record["instance"])
        )
        if not valid:
            raise ValueError("Invalid endpoint")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("No ready Office Bridge endpoint; run bootstrap.ps1 first.") from exc
    return record


def mcp_url(root: Path) -> str:
    return server_record(root)["mcp_url"]


def base_url(root: Path) -> str:
    return mcp_url(root).removesuffix("/mcp")
