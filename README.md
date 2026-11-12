# Office Bridge

Local Outlook and Teams actions for Windows, served by the community Actions Runtime through MCP. Outlook uses classic Outlook COM; background Teams uses an isolated persistent WebView2 session with Windows SSO.

## Run

Requires Windows, classic Outlook, WebView2 Runtime, and `uv` on PATH.

```powershell
./setup.ps1
./.runtime/Scripts/python.exe -m actions.server devenv task install-webview2
./start-office.ps1
```

For local development, use `./start-office.ps1 -Dev`. It enables action auto-reload and disables the API key on loopback. Stop with `./stop-office.ps1`.

- MCP: `http://127.0.0.1:8766/mcp`
- Runtime UI: `http://127.0.0.1:8766`
- Full live API: `http://127.0.0.1:8766/openapi.json`
- Saved API: [docs/api/openapi.json](docs/api/openapi.json)

## Layout

```text
src/
  outlook_actions.py          Outlook and attachment entrypoints
  desktop_teams_actions.py    Windows UI Automation entrypoints
  teams_actions.py            Persistent background Teams entrypoints
  office_bridge/              Models, bridges, services, runtime helpers, native assets
scripts/                      Lifecycle, verification, and development tools
scripts/diagnostics/          Optional browser and workstation diagnostics
tests/                       Offline behavior and isolated browser tests
docs/                        Architecture, operations, and API reference
```

The runtime is served from a generated `.package` containing only `package.yaml` and `src/`. `.state`, `.runtime`, and `.package` are private local/generated directories and are not published.

## Verify

```powershell
./.runtime/Scripts/python.exe -m pytest
./.runtime/Scripts/ruff.exe check src scripts tests
./.runtime/Scripts/python.exe scripts/verify_teams_web.py --dev
```

Omit `--dev` for the authenticated server. `scripts/verify_mcp.py` checks authenticated MCP discovery and read-only Outlook/desktop Teams operations. `scripts/register_mcp.py` registers the verified authenticated endpoint while preserving unrelated Codex configuration.

Sending always requires explicit user authorization. Unknown send outcomes are never automatically retried. Verification does not send messages. See [operations](docs/operations.md) for the separately authorized manual send test, profile configuration, and diagnostics.

See [architecture](docs/architecture.md) for package boundaries and persistent-session ownership.
