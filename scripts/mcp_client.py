"""Small HTTP MCP client shared by repository verification tools."""

import json

import requests

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
        self.protocol = None

    def __enter__(self):
        try:
            response = self.session.post(
                URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "office-bridge-verification", "version": "1.0"},
                    },
                },
                timeout=30,
            )
            initialized = decode(response)["result"]
            if session_id := response.headers.get("Mcp-Session-Id"):
                self.session.headers["Mcp-Session-Id"] = session_id
            self.protocol = initialized["protocolVersion"]
            self.session.headers["MCP-Protocol-Version"] = self.protocol
            self.session.post(
                URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10
            ).raise_for_status()
            return self
        except Exception:
            self.session.close()
            raise

    def __exit__(self, *exc):
        try:
            self.session.delete(URL, timeout=10)
        finally:
            self.session.close()

    def rpc(self, method, params=None):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        reply = decode(self.session.post(URL, json=message, timeout=120))
        if "error" in reply:
            raise RuntimeError(f"MCP {method} failed: {reply['error'].get('code')}")
        return reply["result"]

    def tools(self):
        return self.rpc("tools/list")["tools"]

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
