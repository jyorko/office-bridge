# Office Bridge agent instructions

## Scope and tools

- For using the MCP, read `.agents/skills/office-bridge-mcp/SKILL.md` and the relevant workflow reference. Work tracking requires a user-authorized session/chat opt-in; do not infer it from a passive read or an installation check.

- Use the community `actions-runtime` and `actions-core` packages from `joshyorko/actions`. Do not replace the official server with a custom MCP or HTTP server.
- All subagents must use LUNA unless the user explicitly authorizes another model.
- Read the relevant RCC Action Server skill before changing package or runtime integration.
- Use `from actions import action` and `from robocorp import browser` for browser actions. Let `robocorp-browser` resolve its compatible Playwright dependency.
- Work Items is a runtime dependency. Office Bridge does not use Work Items workflows or storage.

## Setup and ownership

- Fresh clone: `./bootstrap.ps1 -Dev`. Bootstrap reuses the privately saved Teams account or detects the signed-in Windows work account. `-TeamsAccount your.name@company.com` overrides detection. `-Dev` controls auto-reload only; no mode uses an API key. `-PrepareOnly` installs prerequisites without startup; `-SkipCodex` and `-SkipCopilot` independently skip client registration.
- Root `bootstrap.ps1` is the entry point. PowerShell lifecycle scripts belong in `powershell/`; Python lifecycle and verification scripts belong in `scripts/`.
- Implementation belongs in `src/`, including native PowerShell, C#, and JavaScript. Never leave required implementation only in ignored state or installed environments.
- Start: `./powershell/start-office.ps1 -Dev`. Stop: `./powershell/stop-office.ps1`. Inspect the recorded process and listener before changing an existing server. Do not launch duplicate servers.
- The server runs hidden on loopback, preferring port 8766 and using Runtime port zero when unavailable. `.state/server.json` holds the actual MCP URL and server instance. Never guess the endpoint from a fixed port. MCP is `/mcp`, and the full API schema is `/openapi.json`. Startup verifies reads, then removes/re-adds `desktop-office` in both `$CODEX_HOME/config.toml` (default `~/.codex`) and `$COPILOT_HOME/mcp-config.json` (default `~/.copilot`), preserving unrelated settings and reading the result back. Registration connects clients; it does not launch the process.
- Disposable Actions Runtime workers connect to the launcher-owned persistent Teams host. Worker replacement must not replace the browser or replay a request.
- Reload changed native Teams code with `scripts/restart_teams_session.py`, then verify readiness. The server UI stays running; the supervisor's bounded restart policy still applies.

## Private data and desktop protection

- `.runtime` (legacy disposable only), `.package`, `.state`, Python caches, profiles, credentials, mail/chat data, downloads, logs, databases, and binaries must stay out of Git.
- Device data lives under `%LOCALAPPDATA%/OfficeActionServer`: `config` holds the expected account, `teams-webview2-profile` holds the isolated session, `webview2-sdk` holds verified DLLs, and `teams-web-sends` holds durable send records. No API key is generated or registered.
- Bootstrap must work without previous generated folders or browser data. Use Windows work-account SSO and verify the exact account; never extract tokens or copy the user's normal Chrome/Edge profile.
- Keep certificate verification enabled. Configure Windows roots in the actual application RCC home before building workers. Preserve other profile settings and explicit `ACTIONS_HOME` overrides.
- Never take the user's keyboard, clipboard, desktop focus, normal browser, or Teams window. Use the hidden WebView2 controller or an explicitly selected isolated browser.
- Preserve existing composers and verify the exact chat ID and title before reading or sending. Missing or ambiguous identities must stop the action.
- Use the general Teams list/read/send actions with `{chat_id, title}` references. Do not add per-person, per-group, or self-specific action functions or wire operations. Account names and email addresses belong in private configuration, not implementation constants.
- Sending needs explicit user authorization. Reserve requests durably, click once, and verify a new settled message. An uncertain send must never be retried automatically.
- A user-requested full reset removes generated data and backups. Do not preserve or restore private records when the user asks to start fresh. Never replay historical sends or use a send test as an installation check.

## Verification and publication

- Run `action-server devenv --datadir .state/tasks task test`, `action-server devenv --datadir .state/tasks task lint`, and `action-server devenv --datadir .state/tasks task format-check`.
- Use `action-server devenv --datadir .state/tasks task verify-teams-dev` for read-only MCP discovery, exact Teams identity, hidden-host checks, self-chat read, and continuity across managed workers.
- Never replay a historical send test or use a send as a bootstrap check. Inspect existing send history before any separately authorized send test.
- Rebuild the SDK with its pinned package and DLL hash checks. Do not commit downloaded artifacts. Update `docs/api/openapi.json` with `action-server devenv --datadir .state/tasks task export-openapi` when the API changes.
- Inspect the staged diff and scan for private data before committing or pushing. Keep documentation aligned with actual lifecycle paths and installed versions.
- Report reproducible Action Server bugs upstream to `https://github.com/joshyorko/actions/issues` with sanitized steps, versions, expected/actual behavior, and relevant logs. The user authorizes these reports. Resolve package configuration and local integration mistakes here; never publish credentials, corporate messages, receipts, or private identifiers in a report.
