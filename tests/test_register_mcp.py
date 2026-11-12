import json
import os
import sys
import tomllib

import pytest
import register_mcp

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows configuration ACLs")


def verified_install(tmp_path, monkeypatch):
    state = tmp_path / "checkout/.state"
    state.mkdir(parents=True)
    evidence = {
        "verified": True,
        "dev": True,
        "mcp_url": "http://127.0.0.1:8766/mcp",
        "checks": ["teams_web_read_self_chat"],
    }
    (state / "bootstrap-verification.json").write_text(json.dumps(evidence))
    config = tmp_path / "codex/config.toml"
    config.parent.mkdir()
    monkeypatch.setattr(register_mcp, "ROOT", state.parent)
    monkeypatch.setattr(register_mcp, "CONFIG", config)
    monkeypatch.setattr(sys, "argv", ["register_mcp.py", "--dev"])
    return config


def test_fresh_dev_registration_preserves_unrelated_configuration(tmp_path, monkeypatch):
    config = verified_install(tmp_path, monkeypatch)
    original = '[mcp_servers.other]\ncommand = "other-tool"\n'
    config.write_text(original)
    register_mcp.main()
    result = tomllib.loads(config.read_text())
    assert result["mcp_servers"]["other"] == {"command": "other-tool"}
    assert result["mcp_servers"]["desktop-office"]["url"] == "http://127.0.0.1:8766/mcp"
    assert "http_headers" not in result["mcp_servers"]["desktop-office"]
    assert (tmp_path / "checkout/.state/config.toml.before-office").read_text() == original


def test_dev_reuses_existing_authenticated_registration_without_changing_it(tmp_path, monkeypatch):
    config = verified_install(tmp_path, monkeypatch)
    original = (
        '[mcp_servers.desktop-office]\nurl = "http://127.0.0.1:8766/mcp"\n'
        '[mcp_servers.desktop-office.http_headers]\nAuthorization = "Bearer private"\n'
    )
    config.write_text(original)
    register_mcp.main()
    assert config.read_text() == original


def test_different_existing_endpoint_is_preserved(tmp_path, monkeypatch):
    config = verified_install(tmp_path, monkeypatch)
    original = '[mcp_servers.desktop-office]\nurl = "http://other.invalid/mcp"\n'
    config.write_text(original)
    with pytest.raises(SystemExit, match="differs"):
        register_mcp.main()
    assert config.read_text() == original
