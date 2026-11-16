import json
import sys
import tomllib

import pytest
import register_mcp


def verified_install(tmp_path, monkeypatch, *, dev=True, port=49321):
    state = tmp_path / "checkout/.state"
    state.mkdir(parents=True)
    record = {"mcp_url": f"http://127.0.0.1:{port}/mcp", "instance": "current-instance"}
    (state / "server.json").write_text(json.dumps(record))
    evidence = {
        "verified": True,
        "dev": dev,
        "mcp_url": record["mcp_url"],
        "server_instance": record["instance"],
        "checks": ["teams_web_read_chat"],
    }
    (state / "bootstrap-verification.json").write_text(json.dumps(evidence))
    config = tmp_path / "codex/config.toml"
    copilot = tmp_path / "copilot/mcp-config.json"
    config.parent.mkdir()
    copilot.parent.mkdir()
    monkeypatch.setattr(register_mcp, "ROOT", state.parent)
    monkeypatch.setattr(register_mcp, "CONFIG", config)
    monkeypatch.setattr(register_mcp, "COPILOT_CONFIG", copilot)
    monkeypatch.setattr(sys, "argv", ["register_mcp.py"] + (["--dev"] if dev else []))
    monkeypatch.delenv("OFFICE_BRIDGE_SKIP_CODEX", raising=False)
    monkeypatch.delenv("OFFICE_BRIDGE_SKIP_COPILOT", raising=False)
    return config, copilot, record


@pytest.mark.parametrize("dev", [False, True])
def test_registers_both_clients_at_recorded_port_without_keys(tmp_path, monkeypatch, dev):
    config, copilot, record = verified_install(tmp_path, monkeypatch, dev=dev)
    config.write_text(
        '# keep this comment\nmodel = "chosen"\n[mcp_servers.other]\ncommand = "keep"\n'
    )
    copilot.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}, "custom": 5}))
    register_mcp.main()
    codex_data = tomllib.loads(config.read_text())
    copilot_data = json.loads(copilot.read_text())
    assert codex_data["mcp_servers"]["other"] == {"command": "keep"}
    assert copilot_data["mcpServers"]["other"] == {"command": "keep"}
    assert codex_data["model"] == "chosen"
    assert copilot_data["custom"] == 5
    assert "# keep this comment" in config.read_text()
    for entry in (
        codex_data["mcp_servers"]["desktop-office"],
        copilot_data["mcpServers"]["desktop-office"],
    ):
        assert entry["url"] == record["mcp_url"]
        assert "headers" not in entry and "http_headers" not in entry
    assert not (tmp_path / "checkout/.state/config.toml.before-office").exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_removes_old_entries_before_readding_and_drops_stale_auth(tmp_path, monkeypatch):
    config, copilot, record = verified_install(tmp_path, monkeypatch)
    config.write_text(
        '[mcp_servers."desktop-office"]\nurl = "http://old.invalid/mcp"\n[mcp_servers.desktop-office.http_headers]\nAuthorization = "old-value"\n'
    )
    copilot.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "desktop-office": {
                        "type": "http",
                        "url": "http://old.invalid/mcp",
                        "headers": {"Authorization": "old-value"},
                    }
                }
            }
        )
    )
    original_replace = register_mcp.replace_file
    observations = []

    def observe(path, before, content):
        original_replace(path, before, content)
        document = (
            tomllib.loads(path.read_text()) if path == config else json.loads(path.read_text())
        )
        section = "mcp_servers" if path == config else "mcpServers"
        observations.append((path, document.get(section, {}).get("desktop-office")))

    monkeypatch.setattr(register_mcp, "replace_file", observe)
    register_mcp.main()
    assert [entry for _, entry in observations[::2]] == [None, None]
    assert all(entry["url"] == record["mcp_url"] for _, entry in observations[1::2])
    assert all(
        "headers" not in entry and "http_headers" not in entry for _, entry in observations[1::2]
    )


def test_fresh_configs_and_repeat_registration_are_supported(tmp_path, monkeypatch):
    config, copilot, _ = verified_install(tmp_path, monkeypatch)
    register_mcp.main()
    before = (config.read_bytes(), copilot.read_bytes())
    register_mcp.main()
    assert before == (config.read_bytes(), copilot.read_bytes())


@pytest.mark.parametrize(
    "field,value",
    [
        ("server_instance", "previous-instance"),
        ("mcp_url", "http://127.0.0.1:8766/mcp"),
        ("verified", False),
        ("dev", False),
    ],
)
def test_stale_or_wrong_verification_cannot_change_configs(tmp_path, monkeypatch, field, value):
    config, copilot, _ = verified_install(tmp_path, monkeypatch)
    evidence_path = register_mcp.ROOT / ".state/bootstrap-verification.json"
    evidence = json.loads(evidence_path.read_text())
    evidence[field] = value
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(SystemExit, match="Verify this server instance"):
        register_mcp.main()
    assert not config.exists() and not copilot.exists()


def test_malformed_second_client_prevents_changing_either(tmp_path, monkeypatch):
    config, copilot, _ = verified_install(tmp_path, monkeypatch)
    config.write_text('[mcp_servers.other]\ncommand = "keep"\n')
    original = config.read_bytes()
    copilot.write_text('{"mcpServers":')
    with pytest.raises(ValueError):
        register_mcp.main()
    assert config.read_bytes() == original


def test_concurrent_configuration_change_is_preserved(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "new-user-choice"\n')
    with pytest.raises(RuntimeError, match="changed concurrently"):
        register_mcp.replace_file(path, b"old content", 'model = "overwrite"\n')
    assert path.read_text() == 'model = "new-user-choice"\n'
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("skip", ["codex", "copilot"])
def test_can_skip_each_client_independently(tmp_path, monkeypatch, skip):
    config, copilot, _ = verified_install(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["register_mcp.py", "--dev", f"--skip-{skip}"])
    register_mcp.main()
    assert config.exists() is (skip != "codex")
    assert copilot.exists() is (skip != "copilot")


def test_toml_multiline_text_and_inline_tables_are_preserved(tmp_path, monkeypatch):
    config, _, record = verified_install(tmp_path, monkeypatch)
    original = 'note = """\n[mcp_servers.desktop-office]\nliteral text\n"""\nmcp_servers = {other = {command = "keep"}, desktop-office = {url = "http://old.invalid"}}\n'
    config.write_text(original)
    register_mcp.main()
    parsed = tomllib.loads(config.read_text())
    assert parsed["note"] == tomllib.loads(original)["note"]
    assert parsed["mcp_servers"]["other"] == {"command": "keep"}
    assert parsed["mcp_servers"]["desktop-office"]["url"] == record["mcp_url"]
