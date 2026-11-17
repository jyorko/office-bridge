import os
import runpy
import sys
from pathlib import Path

import actions.server.cli
import package_layout
import prepare_state
import pytest

from office_bridge.runtime import local_state, supervisor


@pytest.mark.parametrize("dev", [False, True])
def test_launch_modes_use_loopback_and_selected_port_without_api_key(tmp_path, monkeypatch, dev):
    arguments = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ACTIONS_HOME", str(tmp_path / "actions"))
    monkeypatch.setenv("OFFICE_BRIDGE_ACCOUNT", "fixture@example.com")
    monkeypatch.setenv("OFFICE_BRIDGE_PORT", "0")
    monkeypatch.setattr(local_state, "configured_account", lambda: "fixture@example.com")
    monkeypatch.setattr(package_layout, "stage_package", lambda: None)
    monkeypatch.setattr(package_layout, "watch_sources", lambda: None)
    prepared = []
    monkeypatch.setenv("OFFICE_BRIDGE_TEAMS_ACCOUNT", "fixture@example.com")
    monkeypatch.setattr(
        prepare_state, "prepare_local_state", lambda **kwargs: prepared.append(kwargs)
    )
    monkeypatch.setattr(supervisor, "start", lambda root: None)
    monkeypatch.setattr(actions.server.cli, "main", lambda args: arguments.extend(args))
    monkeypatch.setattr(sys, "argv", ["run_server.py"] + (["--dev"] if dev else []))
    path = Path(__file__).resolve().parents[1] / "scripts/run_server.py"
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(path), run_name="__main__")
    assert result.value.code is None
    assert "--api-key" not in arguments
    assert ("--auto-reload" in arguments) is dev
    assert arguments[arguments.index("--port") + 1] == "0"
    assert arguments[arguments.index("--address") + 1] == "127.0.0.1"
    assert prepared == [{"dev": dev, "teams_account": "fixture@example.com"}]
    assert "OFFICE_BRIDGE_TEAMS_ACCOUNT" not in os.environ
