from copy import deepcopy

import pytest
from mcp_client import MCPClient


class Reply:
    headers = {"content-type": "application/json"}

    def __init__(self, result):
        self.result = result

    def raise_for_status(self):
        pass

    def json(self):
        return {"jsonrpc": "2.0", "id": 1, "result": self.result}


class Session:
    def __init__(self, versions=None):
        self.headers = {}
        self.requests = []
        self.closed = False
        self.versions = versions if versions is not None else ["2026-07-28"]

    def post(self, url, *, json, headers, timeout):
        self.requests.append(deepcopy((json, headers)))
        method = json["method"]
        if method == "server/discover":
            return Reply({"supportedVersions": self.versions})
        if method == "tools/list":
            if json["params"].get("cursor") == "next":
                return Reply({"tools": [{"name": "second"}]})
            return Reply({"tools": [{"name": "first"}], "nextCursor": "next"})
        return Reply({"structuredContent": {"result": {"state": "read"}}})

    def close(self):
        self.closed = True


def test_modern_mcp_discovery_paging_and_per_request_metadata(monkeypatch):
    session = Session()
    monkeypatch.setattr("mcp_client.requests.Session", lambda: session)
    with MCPClient() as client:
        assert [item["name"] for item in client.tools()] == ["first", "second"]
        arguments = {"chat": "test"}
        client.call("read_chat", arguments)
        assert arguments == {"chat": "test"}
    assert session.closed
    assert session.headers["MCP-Protocol-Version"] == "2026-07-28"
    assert [request[0]["method"] for request in session.requests] == [
        "server/discover",
        "tools/list",
        "tools/list",
        "tools/call",
    ]
    for message, headers in session.requests:
        assert headers["MCP-Method"] == message["method"]
        assert message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        assert message["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]["name"]
    assert session.requests[-1][1]["MCP-Name"] == "read_chat"


def test_unsupported_protocol_closes_client_without_legacy_retry(monkeypatch):
    session = Session(["2025-06-18"])
    monkeypatch.setattr("mcp_client.requests.Session", lambda: session)
    with pytest.raises(RuntimeError, match="protocol"), MCPClient():
        pass
    assert session.closed
    assert len(session.requests) == 1
