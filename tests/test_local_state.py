import json

import pytest

from office_bridge.runtime.local_state import configured_account, prepare, settings_path


def test_fresh_checkout_persists_account_without_credentials(tmp_path):
    config = tmp_path / "device"
    first = tmp_path / "first"
    path = prepare(first, config=config, teams_account="Teammate@Example.com")
    assert path == settings_path(config)
    assert json.loads(path.read_text()) == {"teams_account": "teammate@example.com"}
    assert not (first / ".state/.api_key").exists()

    second = tmp_path / "fresh-clone"
    assert prepare(second, config=config) == path
    assert not (second / ".state/.api_key").exists()
    assert configured_account(config) == "teammate@example.com"


def test_fresh_device_requires_an_explicit_account(tmp_path):
    with pytest.raises(ValueError, match="--teams-account"):
        prepare(tmp_path / "checkout", dev=True, config=tmp_path / "device")


def test_existing_account_is_reused_and_explicit_account_updates_it(tmp_path):
    root = tmp_path / "checkout"
    config = tmp_path / "device"
    prepare(root, config=config, teams_account="old@example.com")
    prepare(root, config=config, teams_account="new@example.com")
    assert configured_account(config) == "new@example.com"
    assert not (config / ".api_key").exists()


@pytest.mark.parametrize(
    "invalid",
    ["", "missing-at.example.com", "two@@example.com", "user@invalid", "a" * 65 + "@example.com"],
)
def test_invalid_account_is_rejected_without_creating_settings(tmp_path, invalid):
    config = tmp_path / "device"
    with pytest.raises(ValueError, match="valid email"):
        prepare(tmp_path / "checkout", dev=True, config=config, teams_account=invalid)
    assert not settings_path(config).exists()


def test_removes_existing_key_and_ignores_legacy_target_file(tmp_path):
    root = tmp_path / "checkout"
    state = root / ".state"
    state.mkdir(parents=True)
    (state / ".api_key").write_text("k" * 40)
    (state / "teams-targets.json").write_text('{"self":"48:notes"}')
    config = tmp_path / "device"
    result = prepare(root, dev=True, config=config, teams_account="teammate@example.com")
    assert json.loads(result.read_text()) == {"teams_account": "teammate@example.com"}
    assert not (config / ".api_key").exists()
    assert not (state / ".api_key").exists()
    assert not (config / "teams-targets.json").exists()


def test_removes_obsolete_device_and_checkout_keys(tmp_path):
    root = tmp_path / "checkout"
    config = tmp_path / "device"
    prepare(root, config=config, teams_account="teammate@example.com")
    key = config / ".api_key"
    key.write_text("old-device-key")
    (root / ".state/.api_key").write_text("different" * 5)
    prepare(root, config=config)
    assert not key.exists()
    assert not (root / ".state/.api_key").exists()


@pytest.mark.parametrize("dev", [False, True])
def test_neither_mode_requires_or_generates_credentials(tmp_path, dev):
    root = tmp_path / "checkout"
    config = tmp_path / "device"
    prepare(root, dev=dev, config=config, teams_account="teammate@example.com")
    assert not (root / ".state/.api_key").exists()
    assert not (config / ".api_key").exists()
