"""Small HTTP MCP client shared by repository verification tools."""

import json

import requests
from mcp.shared.inbound import encode_header_value
from mcp_types import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
)

URL = "http://127.0.0.1:8766/mcp"


def decode(response):
    response.raise_for_status()
    if "text/event-stream" in response.headers.get("content-type", ""):
        events = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        return next(item for item in reversed(events) if "id" in item)
    return response.json()


class MCPClient:
    def __init__(self, token: str | None = None):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        )
        if token:
            self.session.headers["Authorization"] = "Bearer " + token
        self.next_id = 1
        self.protocol = "2026-07-28"
        self.session.headers["MCP-Protocol-Version"] = self.protocol

    def __enter__(self):
        try:
            discovery = self.rpc("server/discover")
            if self.protocol not in discovery.get("supportedVersions", []):
                raise RuntimeError("Action Server does not support the expected MCP protocol")
            return self
        except Exception:
            self.session.close()
            raise

    def __exit__(self, *exc):
        self.session.close()

    def rpc(self, method, params=None):
        self.next_id += 1
        params = dict(params or {})
        params["_meta"] = {
            **params.get("_meta", {}),
            PROTOCOL_VERSION_META_KEY: self.protocol,
            CLIENT_INFO_META_KEY: {"name": "office-bridge-verification", "version": "1.1"},
            CLIENT_CAPABILITIES_META_KEY: {},
        }
        headers = {"MCP-Method": method}
        if method in {"tools/call", "prompts/get", "resources/read"}:
            name = params["uri" if method == "resources/read" else "name"]
            headers["MCP-Name"] = encode_header_value(name)
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        reply = decode(self.session.post(URL, json=message, headers=headers, timeout=120))
        if "error" in reply:
            raise RuntimeError(f"MCP {method} failed: {reply['error'].get('code')}")
        return reply["result"]

    def tools(self):
        tools = []
        cursor = None
        for _ in range(20):
            page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(page["tools"])
            following = page.get("nextCursor")
            if not following:
                return tools
            if following == cursor:
                raise RuntimeError("MCP catalog pagination did not advance")
            cursor = following
        raise RuntimeError("MCP catalog exceeded the verification page limit")

    def call(self, name, arguments):
        return self.rpc("tools/call", {"name": name, "arguments": arguments})


def unwrap(result):
    if result.get("isError"):
        raise RuntimeError("MCP action failed")
    value = result.get("structuredContent")
    if value is None:
        value = json.loads(
            "\n".join(
                item["text"] for item in result.get("content", []) if item.get("type") == "text"
            )
        )
    if value.get("error"):
        raise RuntimeError("Action returned an error; inspect the private verification output.")
    return value.get("result", value)
