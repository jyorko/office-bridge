"""Exercise the launcher with real Runtime; substitute Office verification only."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = Path(
    os.environ.get("OFFICE_BRIDGE_TEST_SERVER_EXE", Path.home() / ".local/bin/action-server.exe")
)
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not SERVER.is_file(), reason="installed Windows Runtime required"
)

DRIVER = """
import json, os, socket, subprocess, sys
from pathlib import Path
from _bootstrap import ROOT
from office_bridge.runtime.endpoint import startup_port, server_record
phase = sys.argv[1]
if phase == "serve":
    from actions.server.cli import main
    port = startup_port(int(os.environ["OFFICE_BRIDGE_PORT"]))
    marker = ROOT / ".state/raced-once"
    if os.environ.get("OFFICE_BRIDGE_TEST_BIND_RACE") == "1" and not marker.exists():
        marker.write_text(str(port))
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", port))
        occupied.listen()
    raise SystemExit(main(["start", "--address", "127.0.0.1", "--port", str(port),
        "--actions-sync=false", "--min-processes", "0", "--max-processes", "1",
        "--full-openapi-spec", "--datadir", str(ROOT / ".state/runtime")]))
elif phase == "complete":
    from mcp_client import MCPClient
    with MCPClient() as client:
        client.tools()
    record = server_record(ROOT)
    # Synthetic Office verification is scoped to this disposable fixture.
    (ROOT / ".state/bootstrap-verification.json").write_text(json.dumps({
        "verified": True, "dev": True, "mcp_url": record["mcp_url"],
        "server_instance": record["instance"], "checks": ["teams_web_read_chat"]}))
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/register_mcp.py"), "--dev"],
        check=True,
    )
"""


def run_script(path, env, *args):
    # File output avoids waiting on pipe handles inherited by the background server.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(path),
                *args,
            ],
            env=env,
            stdout=output,
            stderr=output,
            timeout=240,
        )
        output.seek(0)
        assert result.returncode == 0, output.read()[-10000:]


def test_real_runtime_fallback_reuse_registration_and_bind_race(tmp_path):
    project = tmp_path / "project with spaces"
    (project / "powershell").mkdir(parents=True)
    (project / "scripts").mkdir()
    for name in ("start-office.ps1", "stop-office.ps1"):
        shutil.copyfile(ROOT / "powershell" / name, project / "powershell" / name)
    for name in ("_bootstrap.py", "register_mcp.py", "mcp_client.py"):
        shutil.copyfile(ROOT / "scripts" / name, project / "scripts" / name)
    shutil.copytree(
        ROOT / "src/office_bridge",
        project / "src/office_bridge",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (project / "scripts/driver.py").write_text(DRIVER)
    manifest = (ROOT / "package.yaml").read_text()
    manifest = (
        manifest[: manifest.index("dev-tasks:")]
        + """dev-tasks:
  serve-dev: python scripts/driver.py serve
  complete-startup-dev: python scripts/driver.py complete
  stop-host: python scripts/driver.py stop
"""
    )
    (project / "package.yaml").write_text(manifest)
    allowed_environment = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "ACTIONS_HOME",
        "ROBOCORP_HOME",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed_environment}
    env.update(
        CODEX_HOME=str(tmp_path / "codex"),
        COPILOT_HOME=str(tmp_path / "copilot"),
    )
    original_userprofile = env.get("USERPROFILE")
    assert original_userprofile
    expected_server = Path(original_userprofile, ".local", "bin", "action-server.exe")
    assert expected_server.resolve() == SERVER.resolve()
    env.pop("OFFICE_BRIDGE_TEST_BIND_RACE", None)
    codex = Path(env["CODEX_HOME"]) / "config.toml"
    copilot = Path(env["COPILOT_HOME"]) / "mcp-config.json"
    start = project / "powershell/start-office.ps1"
    stop = project / "powershell/stop-office.ps1"
    record_file = project / ".state/server.json"

    def assert_clients(record):
        assert (
            tomllib.loads(codex.read_text())["mcp_servers"]["desktop-office"]["url"]
            == record["mcp_url"]
        )
        assert (
            json.loads(copilot.read_text())["mcpServers"]["desktop-office"]["url"]
            == record["mcp_url"]
        )
        assert "Authorization" not in codex.read_text() + copilot.read_text()
        assert not list(project.rglob(".api_key"))

    try:
        assert env["USERPROFILE"] == original_userprofile
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            preferred = occupied.getsockname()[1]
            run_script(start, env, "-Dev", "-Port", str(preferred))
            first = json.loads(record_file.read_text())
            assert f":{preferred}/" not in first["mcp_url"]
            assert_clients(first)
            codex.write_text(
                '[mcp_servers.desktop-office]\nurl = "http://old.invalid/mcp"\n[mcp_servers.desktop-office.http_headers]\nAuthorization = "obsolete"\n'
            )
            copilot.write_text('{"mcpServers":{"desktop-office":{"url":"http://old.invalid/mcp"}}}')
            run_script(start, env, "-Dev")
            assert json.loads(record_file.read_text())["instance"] == first["instance"]
            assert_clients(first)
        run_script(stop, env)
        assert not record_file.exists()
        env["OFFICE_BRIDGE_TEST_BIND_RACE"] = "1"
        run_script(start, env, "-Dev", "-Port", str(preferred))
        second = json.loads(record_file.read_text())
        assert second["instance"] != first["instance"]
        assert f":{preferred}/" not in second["mcp_url"]
        assert (project / ".state/raced-once").read_text() == str(preferred)
        assert second["startup_attempt"] == 2
        assert_clients(second)
    finally:
        if record_file.exists():
            run_script(stop, env)
