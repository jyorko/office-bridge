import json
import socket

import pytest

from office_bridge.runtime.endpoint import base_url, mcp_url, server_record, startup_port


def test_occupied_port_uses_runtime_os_allocation():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        assert startup_port(occupied.getsockname()[1]) == 0


def test_available_preferred_port_is_kept():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        preferred = probe.getsockname()[1]
    assert startup_port(preferred) == preferred
    assert startup_port(0) == 0


@pytest.mark.parametrize("port", [-1, 65536])
def test_invalid_ports_are_rejected(port):
    with pytest.raises(ValueError):
        startup_port(port)


def test_reads_actual_endpoint_instead_of_default(tmp_path):
    state = tmp_path / ".state"
    state.mkdir()
    (state / "server.json").write_text(
        json.dumps({"mcp_url": "http://127.0.0.1:49127/mcp", "instance": "current"})
    )
    assert mcp_url(tmp_path) == "http://127.0.0.1:49127/mcp"
    assert base_url(tmp_path) == "http://127.0.0.1:49127"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://0.0.0.0:8766/mcp",
        "http://example.com:8766/mcp",
        "http://secret@127.0.0.1:8766/mcp",
        "http://127.0.0.1:0/mcp",
        "http://127.0.0.1:8766/other",
        "http://127.0.0.1:8766/mcp?key=value",
    ],
)
def test_unready_or_nonlocal_record_is_rejected(tmp_path, url):
    state = tmp_path / ".state"
    state.mkdir()
    (state / "server.json").write_text(json.dumps({"mcp_url": url, "instance": "current"}))
    with pytest.raises(RuntimeError, match="No ready Office Bridge endpoint"):
        server_record(tmp_path)


def test_missing_record_does_not_fall_back_to_unrelated_default_listener(tmp_path):
    with pytest.raises(RuntimeError, match="No ready Office Bridge endpoint"):
        mcp_url(tmp_path)
