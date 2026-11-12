# Operations

## Server lifecycle

Run `./bootstrap.ps1 -Dev -TeamsAccount your.name@company.com` after cloning. It installs prerequisites, configures RCC trust, starts the server, verifies MCP and a Teams self-chat read, and registers Codex. The account is saved privately, so later starts can omit the flag. `-PrepareOnly` stops after prerequisite installation. `-SkipCodex` leaves Codex configuration alone. The default without `-Dev` requires a private API key and verifies that unauthenticated MCP requests are refused.

The server binds to `127.0.0.1:8766` and runs hidden under the current Windows account. It is not a Windows service and does not start at logon. After setup, use `powershell/start-office.ps1 -Dev` to enable official auto-reload without an API key. Omit `-Dev` for authenticated operation. `powershell/stop-office.ps1` closes the isolated Teams host and stops only the verified launcher tree. Start refuses an unrelated listener on the port.

Codex registration uses `$CODEX_HOME/config.toml`, or `~/.codex/config.toml` by default, and preserves other servers. Restart or reconnect the MCP client after first registration. Existing registration for another endpoint is preserved for inspection rather than overwritten.

Python action changes reload automatically in development. Changes to `src/office_bridge/native/teams/` require a server restart. Check `teams_web_status.restart_required` before sends.

## Teams configuration and behavior

The dedicated profile is `%LOCALAPPDATA%/OfficeActionServer/teams-webview2-profile`. The expected account is saved in `config/settings.json` under that application data directory. There are no hardcoded people or chat names in the action interface.

A fresh Windows installation needs no old WebView2 profile or DLLs. Bootstrap downloads and verifies the SDK, detects or installs the Evergreen Runtime, and the tracked native code creates a new profile using Windows work-account SSO. Bootstrap verifies the exact account before declaring success. Organization sign-in requirements can still require user interaction on a new device; old cookies are not part of the source code.

`teams_web_list_chats` returns bounded chat references from the loaded sidebar, optionally filtered by exact name. Pass a `{chat_id, title}` reference to `teams_web_read_chat` or `teams_web_send_chat`. Both verify the exact sidebar row and actual chat header. The same interface handles self chats and groups. Duplicate names have distinct IDs; never choose a recipient from its name alone.

Reads return bounded, currently loaded history. Sends check the configured account, conversation ID, title, and composer, reserve a request ID durably, click once, and observe a settled message. Never automatically retry an uncertain send. Preserve existing drafts when navigating or sending.

Send confirmation correlates a settled message ID, timestamp, and exact body after the single controlled click. It does not independently verify an immutable sender ID. Run installation checks without sending messages.

The optional desktop Teams tools use Windows UI Automation. Reading the current chat can work in the background, but navigation that would activate Teams is refused. Do not relax that protection to make a test pass.

## Verification and API reference

```powershell
./.runtime/Scripts/python.exe -m pytest
./.runtime/Scripts/ruff.exe check src scripts tests
./.runtime/Scripts/python.exe scripts/verify_teams_web.py --dev --count 3
./.runtime/Scripts/python.exe scripts/export_openapi.py
```

The Teams verifier uses fresh MCP sessions, verifies the exact work account and hidden host, and checks session continuity. It never sends messages. `scripts/bootstrap_check.py` additionally reads the self-chat without recording message contents. `scripts/register_mcp.py` accepts that verification, with `--dev` for development. The separate `scripts/verify_mcp.py` expects authentication and checks Outlook and desktop Teams reads.

Completed send receipts and audits remain private. A cold installation check must not replay a historical send request.

## Optional diagnostics

`scripts/diagnostics/teams_login.py` is a coordinated visible Chromium/Edge sign-in helper, not the normal WebView2 path. The matching Chromium binary can be installed with the package's `install-browser` development task. Chromium, installed Edge, and WebView2 use separate profiles.

Installed Edge previously refused DevTools attachment to the Teams page. No explicit DevTools policy was established; Defender for Cloud Apps protection remains a possible cause. The normal background path uses the public WebView2 controller API and Windows SSO.

`powershell/diagnostics/repair-rcc-network.ps1` is the older workstation repair utility. Normal bootstrap configures the application's own RCC home at `%LOCALAPPDATA%/OfficeActionServer/rcc-actions`, honoring an explicit `ACTIONS_HOME` override. It merges Windows trusted roots into the active profile while retaining other settings and leaves certificate verification enabled. `ssl-no-revoke` handles the observed Netskope certificates lacking revocation endpoints. Windows policy and shared RCC homes are unchanged. Private exported profiles stay under `.state`.

## Local data

`.state` holds the checkout's credential copy, lifecycle control, runtime history, and verification evidence. `.runtime` is the launcher/development environment. `.package` is generated package input. Pytest, Ruff, and Python caches are disposable. None contains implementation that must be recovered before installing from Git.

The device API key and configured account live in `%LOCALAPPDATA%/OfficeActionServer/config`. Startup migrates the old checkout key without rotating it and restores the checkout copy after a reclone. Conflicting keys stop startup without overwriting either key. Development mode does not create or require a key.

Downloads, profiles, SDK binaries, and durable send ledgers remain under `%LOCALAPPDATA%/OfficeActionServer`. Attachment downloads are bounded to 25 files/50 MiB per call, with sanitized names, actual saved-byte checks, and SHA256 receipts. Outlook scans are bounded to the newest 500 items.

Bootstrap can recreate software and an empty profile after that directory is removed. It cannot recreate downloaded receipts, historic send ledgers, or a lost API key. Preserve those private records before a reset. An empty install verifies reads only; do not replay old send requests after removing their ledger.

The SDK installer verifies the pinned Microsoft NuGet package and extracted DLL hashes. The bootstrap detects the installed WebView2 Runtime and installs the official Evergreen Runtime only when missing. Runtime versions are `actions-runtime==1.1.0` and `actions-core==1.1.0`. The Windows release executable is pinned to the same version with SHA-256 checks. Work Items is a transitive runtime dependency; Office Bridge does not use it.

Verification uses the Runtime's stateless MCP `2026-07-28` protocol with `server/discover` and per-request routing metadata.
