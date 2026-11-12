"""Keep device configuration independent of disposable checkout state."""

import json
import os
import re
import secrets
import tempfile
from pathlib import Path

_EMAIL = re.compile(
    r"(?=.{1,254}\Z)(?=.{1,64}@)[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\Z"
)


def config_directory() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "OfficeActionServer" / "config"


def settings_path(config: Path | None = None) -> Path:
    directory = config if config is not None else config_directory()
    return directory / "settings.json"


def _account(value: object) -> str:
    if not isinstance(value, str) or not _EMAIL.fullmatch(value.strip()):
        raise ValueError("Teams work account must be a valid email address")
    return value.strip().lower()


def load_settings(config: Path | None = None) -> dict[str, str]:
    path = settings_path(config)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Teams account settings are missing or invalid; rerun bootstrap with --teams-account"
        ) from exc
    if not isinstance(value, dict) or "teams_account" not in value:
        raise ValueError(
            "Teams account settings are missing or invalid; rerun bootstrap with --teams-account"
        )
    settings = dict(value)
    settings["teams_account"] = _account(settings["teams_account"])
    return settings


def configured_account(config: Path | None = None) -> str:
    return load_settings(config)["teams_account"]


def _write_settings(path: Path, settings: dict[str, str], *, create: bool) -> None:
    content = json.dumps(settings, indent=2) + "\n"
    if create:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return
    fd, temporary_name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare_settings(config: Path, teams_account: str | None) -> Path:
    path = settings_path(config)
    if path.exists():
        settings = load_settings(config)
        if teams_account is not None:
            account = _account(teams_account)
            if account != settings["teams_account"]:
                settings["teams_account"] = account
                _write_settings(path, settings, create=False)
        return path
    if teams_account is None:
        raise ValueError(
            "Teams work account is not configured. Rerun bootstrap with --teams-account <work-email>."
        )
    settings = {"teams_account": _account(teams_account)}
    try:
        _write_settings(path, settings, create=True)
    except FileExistsError:
        # Another bootstrap may have won the create race; use its persisted value.
        load_settings(config)
    return path


def _create(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(
    root: Path,
    *,
    dev: bool = False,
    config: Path | None = None,
    teams_account: str | None = None,
) -> Path:
    """Load or set the persistent work account and preserve existing API credentials."""
    config = config if config is not None else config_directory()
    config.mkdir(parents=True, exist_ok=True)
    state = root / ".state"
    state.mkdir(parents=True, exist_ok=True)
    settings = _prepare_settings(config, teams_account)

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
    return settings
