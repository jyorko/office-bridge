# Office Bridge

Local Outlook and Teams actions for Windows, served by the community Actions Runtime through MCP. Outlook uses classic Outlook COM; background Teams uses an isolated persistent WebView2 session with Windows SSO.

## Run

Run from a fresh clone on Windows, under your signed-in work account:

```powershell
./bootstrap.ps1 -Dev
```

Bootstrap installs the pinned Actions Runtime executable, prepares RCC with Windows certificate trust, installs the verified WebView2 SDK and any missing WebView2 Runtime, starts the background server, verifies Teams through MCP, and registers the endpoint in Codex and Copilot. It never sends a test message. Classic Outlook must be installed and configured for Outlook actions.

Bootstrap reuses the privately saved Teams account, or detects your signed-in Windows work account on a fresh device. Use `-TeamsAccount your.name@company.com` to override it or when Windows cannot provide a work-account address. Teams must still match that exact account. Setup also installs the checksum-verified Windows Actions Runtime executable in `~/.local/bin`.

`package.yaml` keeps the pinned Actions Runtime, action dependencies, and development tools in one base dependency set. RCC provisions one package environment for managed tasks; the first task warms it, and later starts, completion checks, tests, lint, and format checks reuse it. There is no separate bootstrap-only Python environment.

Bootstrap installs the MCP usage and bounded safety verification skills into `~/.agents/skills`, including with `-PrepareOnly`, so agents outside this repository can use them. Installed references identify this checkout. Updates preserve unrelated skills and refuse to overwrite locally edited managed files. To refresh skills without starting or restarting anything, run `action-server devenv --datadir .state/tasks task install-skills`.

`-Dev` enables action auto-reload. Both modes run on loopback without an API key. Use `-PrepareOnly` to install prerequisites without starting. `-SkipCodex` and `-SkipCopilot` independently skip client registration. For a prepared installation, start with `./powershell/start-office.ps1 -Dev` and stop with `./powershell/stop-office.ps1`. Startup selects `serve` or `serve-dev`, then runs the matching `complete-startup` or `complete-startup-dev` task to verify the live server before registering clients.

The server runs hidden in your Windows user session. It is not a Windows service or a logon task. Clients connect to that running server; registration does not launch it. Startup prefers port 8766; when it is unavailable, Runtime asks Windows for a free port. Use `-Port 0` to always request a free port. Startup prints the actual URL and records it in `.state/server.json`; verification and both client registrations use that record.

- MCP: `<printed server URL>/mcp`
- Runtime UI: the server URL printed at startup
- Full live API: `<printed server URL>/openapi.json`
- Saved API: [docs/api/openapi.json](docs/api/openapi.json)

## Message views

Outlook mail, Teams conversations and send receipts share a reading view alongside each action’s original result. Inbox rows expand to show returned mail details. The unread filter, history previews and message disclosures work locally. The native MCP App and T3 show the view inline. Ordinary Codex opens the same document in a dedicated, non-activating WebView2 window. The viewer receives HTML over a pipe, uses an InPrivate controller and reuses an open window across action workers. The [presentation guide](.agents/skills/office-bridge-mcp/references/presentation.md) covers client discovery, explicit window opening in T3 and text fallback.

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

All Teams WebView2 implementation code is in `src/office_bridge/native/teams/`. Its SDK installer and process supervisor are in `src/office_bridge/runtime/`. Primary, auxiliary and Copilot WebView2 controllers allow browser audio and report their mute state through `teams_web_status`. Bootstrap recreates generated files from those sources. See [local data](docs/operations.md#local-data) for the separate browser profile and private configuration.

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

`teams_web_send_chat` supports genuine person mentions in plain-text messages. Supply a structured plan with a unique `key`, the exact picker `display_label`, and `recipient.email`, and put `{{mention:key}}` exactly once in the body. The hidden host verifies the picker candidate's exact email/UPN and organizational identity, selects it, and checks the real mention chip before its single send click. Both durable reservations bind the mention plan. Receipts verify the new own message and its settled mention entities; notification delivery is not verified. Status advertises `person_mentions_v1` when authenticated. Missing, ambiguous, or changed candidates stop the operation. Display-name-only lookup and fabricated mention markup are unsupported. `teams_web_inspect_mention_evidence` returns bounded DOM structure and hashed value shapes from an existing settled mention without returning message text or identity values, and preserves an existing composer.

Sending and editing require explicit user authorization. Editing also requires the exact message ID and its current text. Unknown outcomes are never automatically retried. Verification does not send or edit messages. See [operations](docs/operations.md) for configuration and diagnostics.

Use `teams_web_message_link` with an exact chat reference and a message ID from `teams_web_read_chat` to get a direct message link without using the clipboard. `teams_web_message_reactions` returns the supported reaction choices, stable emoji IDs, current message content without reaction summaries, and whether your reaction is already selected. `teams_web_react_message` requires user authorization, the exact message text, a listed reaction name, and a unique request ID. It adds a reaction once, never toggles an existing reaction off, and blocks replay after an uncertain outcome. Reaction support includes the standard toolbar and emoji currently loaded in the More reactions picker, including Hundred points.

Optional [Eyes work tracking](docs/operations.md#eyes-work-tracking) adds Eyes while an assigned task is actively being investigated and removes only an agent-owned marker after the last active claim ends. It requires an explicit session/chat opt-in. Pre-existing Eyes stays untouched; replies and completion reactions are separate. The repo includes an [Office Bridge MCP skill](.agents/skills/office-bridge-mcp/SKILL.md) for using these workflows.

Evidence actions create a private ZIP containing a handoff, explicit source records, and checksum manifest. After user authorization, a delivery action sends the selected pack to a named Devsy workspace and verifies the received hashes. See [evidence delivery](docs/operations.md#evidence-packs-and-devsy-delivery) for prerequisites and limits.

Copilot actions use a separate hidden Teams pane with Work IQ enabled. Check `teams_copilot_status`, submit an authorized question once with `teams_copilot_ask`, then call `teams_copilot_read` with the same request ID until its response is settled. Results contain the answer and observed inline source links. See [Copilot operations](docs/operations.md#copilot-work-iq) for limits and recovery behavior.

Use `teams_copilot_models` to discover the available dropdown choices. The optional `model` argument on `teams_copilot_ask` selects an enabled choice; omitting it keeps the current selection.

See [architecture](docs/architecture.md) for package boundaries and persistent-session ownership.
