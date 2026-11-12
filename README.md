# Office Bridge

Local Outlook and Teams actions for Windows, served by the community Actions Runtime through MCP. Outlook uses classic Outlook COM; background Teams uses an isolated persistent WebView2 session with Windows SSO.

## Run

Run from a fresh clone on Windows, under your signed-in work account:

```powershell
./bootstrap.ps1 -Dev -TeamsAccount your.name@company.com
```

Bootstrap installs the Python launcher, prepares RCC with Windows certificate trust, installs the verified WebView2 SDK and any missing WebView2 Runtime, starts the background server, verifies Teams through MCP, and registers the endpoint in Codex. It never sends a test message. Classic Outlook must be installed and configured for Outlook actions.

The account is saved in private device configuration. Later starts and reclones can omit `-TeamsAccount`. Setup also installs the checksum-verified Windows Actions Runtime executable in `~/.local/bin`.

`-Dev` enables action auto-reload without an API key on loopback. Omit it for authenticated operation. Use `-PrepareOnly` to install prerequisites without starting, or `-SkipCodex` to skip registration. Start and stop an existing installation with `./powershell/start-office.ps1 -Dev` and `./powershell/stop-office.ps1`.

The server runs hidden in your Windows user session. It is not a Windows service or a logon task. Codex connects to that running server; registration does not launch it.

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
bootstrap.ps1                 Fresh-clone entry point
powershell/                   Setup, start, stop, and RCC diagnostics
scripts/                      Python lifecycle, verification, and development tools
scripts/diagnostics/          Optional browser diagnostics
tests/                       Offline behavior and isolated browser tests
docs/                        Architecture, operations, and API reference
```

The runtime is served from a generated `.package` containing only `package.yaml` and `src/`. `.state`, `.runtime`, and `.package` are private local/generated directories and are not published.

All WebView2 implementation code is in `src/office_bridge/native/teams/`. Its SDK installer and process supervisor are in `src/office_bridge/runtime/`. Bootstrap recreates generated files from those sources. See [local data](docs/operations.md#local-data) for the separate browser profile and private configuration.

## Verify

```powershell
./.runtime/Scripts/python.exe -m pytest
./.runtime/Scripts/ruff.exe check src scripts tests
./.runtime/Scripts/python.exe scripts/verify_teams_web.py --dev
```

Omit `--dev` for the authenticated server. `scripts/bootstrap_check.py --dev` verifies MCP discovery, the hidden Teams session, and a self-chat read. `scripts/verify_mcp.py` also checks authenticated Outlook and desktop Teams reads. Registration preserves unrelated Codex configuration.

Teams exposes general chat list, read, and send actions. Choose a `{chat_id, title}` reference from the chat list; reads and sends verify it against the actual chat header. The same actions handle self chats and group chats.

Sending always requires explicit user authorization. Unknown send outcomes are never automatically retried. Verification does not send messages. See [operations](docs/operations.md) for configuration and diagnostics.

See [architecture](docs/architecture.md) for package boundaries and persistent-session ownership.
