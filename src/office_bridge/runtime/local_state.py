"""Keep device configuration independent of disposable checkout state."""

import json
import os
import re
import secrets
from pathlib import Path


def config_directory() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "config"


def _targets(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("self") != "48:notes":
        raise ValueError("Invalid Teams self-chat configuration")
    if "group" in value and (
        not isinstance(value["group"], str)
        or not re.fullmatch(r"19:[0-9a-f]{32}@thread\.v2", value["group"])
    ):
        raise ValueError("Invalid pinned Teams group ID")
    return value


def _create(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(root: Path, *, dev: bool = False, config: Path | None = None) -> Path:
    """Migrate existing settings once, preserve keys, and seed a fresh self-chat."""
    config = config if config is not None else config_directory()
    config.mkdir(parents=True, exist_ok=True)
    state = root / ".state"
    state.mkdir(parents=True, exist_ok=True)
    targets = config / "teams-targets.json"
    legacy_targets = state / "teams-targets.json"
    if not targets.exists():
        value = _targets(legacy_targets) if legacy_targets.exists() else {"self": "48:notes"}
        _create(targets, json.dumps(value, indent=2) + "\n")
    _targets(targets)

    key = config / ".api_key"
    legacy_key = state / ".api_key"
    if not key.exists():
        if legacy_key.exists():
            value = legacy_key.read_text(encoding="utf-8").strip()
            if len(value) < 32:
                raise ValueError("Existing API key is invalid; it was not replaced")
            _create(key, value)
        elif not dev:
            _create(key, secrets.token_urlsafe(32))
    if key.exists():
        value = key.read_text(encoding="utf-8").strip()
        if len(value) < 32:
            raise ValueError("Device API key is invalid; it was not replaced")
        if legacy_key.exists():
            if legacy_key.read_text(encoding="utf-8").strip() != value:
                raise ValueError("Checkout and device API keys differ; neither was replaced")
        else:
            _create(legacy_key, value)
    return targets
