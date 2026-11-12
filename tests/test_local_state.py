import json

import pytest

from office_bridge.runtime.local_state import prepare


def test_fresh_checkout_seeds_self_chat_and_reuses_device_credentials(tmp_path):
    config = tmp_path / "device"
    first = tmp_path / "first"
    path = prepare(first, config=config)
    assert json.loads(path.read_text()) == {"self": "48:notes"}
    key = (first / ".state/.api_key").read_text()
    assert len(key) >= 32
    second = tmp_path / "fresh-clone"
    assert prepare(second, config=config) == path
    assert (second / ".state/.api_key").read_text() == key


def test_migration_preserves_existing_key_and_pinned_destination(tmp_path):
    root = tmp_path / "checkout"
    state = root / ".state"
    state.mkdir(parents=True)
    targets = {"self": "48:notes", "group": "19:" + "a" * 32 + "@thread.v2"}
    (state / "teams-targets.json").write_text(json.dumps(targets))
    (state / ".api_key").write_text("k" * 40)
    config = tmp_path / "device"
    result = prepare(root, dev=True, config=config)
    assert json.loads(result.read_text()) == targets
    assert (config / ".api_key").read_text() == "k" * 40


def test_conflicting_key_is_never_overwritten(tmp_path):
    root = tmp_path / "checkout"
    config = tmp_path / "device"
    prepare(root, config=config)
    key = config / ".api_key"
    original = key.read_bytes()
    (root / ".state/.api_key").write_text("different" * 5)
    with pytest.raises(ValueError, match="differ"):
        prepare(root, config=config)
    assert key.read_bytes() == original


def test_dev_does_not_require_or_generate_credentials(tmp_path):
    root = tmp_path / "checkout"
    config = tmp_path / "device"
    prepare(root, dev=True, config=config)
    assert not (root / ".state/.api_key").exists()
    assert not (config / ".api_key").exists()
