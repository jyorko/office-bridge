# Office Bridge

Local Outlook and Teams actions for Windows, served by the community Actions Runtime through MCP. Outlook uses classic Outlook COM; background Teams uses an isolated persistent WebView2 session with Windows SSO.

## Run

Run from a fresh clone on Windows, under your signed-in work account:

```powershell
./bootstrap.ps1 -Dev
```

Bootstrap installs the pinned Actions Runtime executable, prepares RCC with Windows certificate trust, installs the verified WebView2 SDK and any missing WebView2 Runtime, starts the background server, verifies Teams through MCP, and registers the endpoint in Codex and Copilot. It never sends a test message. Classic Outlook must be installed and configured for Outlook actions.

Bootstrap reuses the privately saved Teams account, or detects your signed-in Windows work account on a fresh device. Use `-TeamsAccount your.name@company.com` to override it or when Windows cannot provide a work-account address. Teams must still match that exact account. Setup also installs the checksum-verified Windows Actions Runtime executable in `~/.local/bin`.

`-Dev` enables action auto-reload. Both modes run on loopback without an API key. Use `-PrepareOnly` to install prerequisites without starting. `-SkipCodex` and `-SkipCopilot` independently skip client registration. Start and stop an existing installation with `./powershell/start-office.ps1 -Dev` and `./powershell/stop-office.ps1`.

The server runs hidden in your Windows user session. It is not a Windows service or a logon task. Clients connect to that running server; registration does not launch it. Startup prefers port 8766; when it is unavailable, Runtime asks Windows for a free port. Use `-Port 0` to always request a free port. Startup prints the actual URL and records it in `.state/server.json`; verification and both client registrations use that record.

- MCP: `<printed server URL>/mcp`
- Runtime UI: the server URL printed at startup
- Full live API: `<printed server URL>/openapi.json`
- Saved API: [docs/api/openapi.json](docs/api/openapi.json)

## Layout

```text
src/
  outlook_actions.py          Outlook and attachment entrypoints
  desktop_teams_actions.py    Windows UI Automation entrypoints
  teams_actions.py            Persistent background Teams entrypoints
  copilot_actions.py          Microsoft 365 Copilot Work IQ queries
  evidence_actions.py         Evidence bundle creation and Devsy delivery
  office_bridge/              Models, bridges, services, runtime helpers, native assets
bootstrap.ps1                 Fresh-clone entry point
powershell/                   Setup, start, stop, and RCC diagnostics
scripts/                      Python lifecycle, verification, and development tools
scripts/diagnostics/          Optional browser diagnostics
tests/                       Offline behavior and isolated browser tests
docs/                        Architecture, operations, and API reference
```

The official Actions Runtime serves the project directly from `package.yaml` and `src/`. `.state` stores private local task state. `.runtime` is an ignored legacy disposable environment; it is not required. `.package` is generated and not published.

All WebView2 implementation code is in `src/office_bridge/native/teams/`. Its SDK installer and process supervisor are in `src/office_bridge/runtime/`. Bootstrap recreates generated files from those sources. See [local data](docs/operations.md#local-data) for the separate browser profile and private configuration.

## Verify

```powershell
action-server devenv --datadir .state/tasks task test
action-server devenv --datadir .state/tasks task lint
action-server devenv --datadir .state/tasks task format-check
action-server devenv --datadir .state/tasks task verify-teams-dev
```

These commands use `ACTIONS_HOME` from the current environment. Bootstrap defaults it to `%LOCALAPPDATA%/OfficeActionServer/rcc-actions`; preserve an explicit override so tasks use the intended RCC profile and TLS trust.

The `verify-teams-dev` task verifies MCP discovery, the hidden Teams session, a self-chat read, and worker continuity. `scripts/verify_mcp.py` also checks Outlook and desktop Teams reads. Each start removes and recreates only the `desktop-office` entry in Codex and Copilot, then reads both configurations back. Other entries are preserved; no configuration backups are created.

Teams exposes general chat search, list, read, send, and edit actions. Use `teams_web_find_chats` to search the Teams directory, or `teams_web_list_chats` for currently loaded chats. Choose a `{chat_id, title}` reference; actions verify it against the actual chat header. The same actions handle self chats and group chats. Sending and editing accept plain text or supported Markdown for bold, italic, inline code, lists, and HTTP(S) links.

Sending and editing require explicit user authorization. Editing also requires the exact message ID and its current text. Unknown outcomes are never automatically retried. Verification does not send or edit messages. See [operations](docs/operations.md) for configuration and diagnostics.

Evidence actions create a private ZIP containing a handoff, explicit source records, and checksum manifest. After user authorization, a delivery action sends the selected pack to a named Devsy workspace and verifies the received hashes. See [evidence delivery](docs/operations.md#evidence-packs-and-devsy-delivery) for prerequisites and limits.

Copilot actions use a separate hidden Teams pane with Work IQ enabled. Check `teams_copilot_status`, submit an authorized question once with `teams_copilot_ask`, then call `teams_copilot_read` with the same request ID until its response is settled. Results contain the answer and observed inline source links. See [Copilot operations](docs/operations.md#copilot-work-iq) for limits and recovery behavior.

Use `teams_copilot_models` to discover the available dropdown choices. The optional `model` argument on `teams_copilot_ask` selects an enabled choice; omitting it keeps the current selection.

See [architecture](docs/architecture.md) for package boundaries and persistent-session ownership.
