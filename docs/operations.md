# Operations

## Server lifecycle

Run `setup.ps1`, then `start-office.ps1`. The default server uses an existing private API key under `.state`; it binds to `127.0.0.1:8766`. `start-office.ps1 -Dev` uses official auto-reload without an API key on the same loopback port. It does not change the stored key. `stop-office.ps1` closes the isolated Teams host and stops only the verified launcher tree.

Python action changes reload automatically in development. Changes to `src/office_bridge/native/teams/` require a server restart. Check `teams_web_status.restart_required` before sends.

## Teams configuration and behavior

The dedicated profile is `%LOCALAPPDATA%/OfficeActionServer/teams-webview2-profile`. Private pinned conversation IDs are in `.state/teams-targets.json`, with `self` and `group` keys. Self-chat uses `48:notes`; provision a group ID only after read-only verification of the intended chat. Missing or ambiguous identities fail closed.

Background actions cover the specified QA group and self-chat. Reads return bounded, currently loaded history. Sends check the account, pinned conversation, title, and composer, reserve a request ID durably, click once, and observe a settled message. Never automatically retry an uncertain send. Preserve existing drafts when navigating or sending.

Group confirmation correlates settled message ID, timestamp, exact body, and sender display name. It does not independently verify an immutable sender ID. Group and self sends have previously been observed live. This layout cleanup uses offline and read-only verification to avoid sending duplicate messages.

The optional desktop Teams tools use Windows UI Automation. Reading the current chat can work in the background, but navigation that would activate Teams is refused. Do not relax that protection to make a test pass.

## Verification and API reference

```powershell
./.runtime/Scripts/python.exe -m pytest
./.runtime/Scripts/ruff.exe check src scripts tests
./.runtime/Scripts/python.exe scripts/verify_teams_web.py --dev --count 3
./.runtime/Scripts/python.exe scripts/export_openapi.py
```

The Teams verifier uses fresh MCP sessions, verifies the exact work account and hidden host, and checks session continuity. It never sends messages. The separate `scripts/verify_mcp.py` expects authentication and checks Outlook and desktop Teams reads. Run `scripts/register_mcp.py` only after that authenticated verification.

`scripts/self_roundtrip.py` is an explicit, manual mutation test. It can send email and Teams messages. Do not use it as a smoke test or rerun a completed send audit. Existing send receipts and audits must remain private.

## Optional diagnostics

`scripts/diagnostics/teams_login.py` is a coordinated visible Chromium/Edge sign-in helper, not the normal WebView2 path. The matching Chromium binary can be installed with the package's `install-browser` development task. Chromium, installed Edge, and WebView2 use separate profiles.

Installed Edge previously refused DevTools attachment to the Teams page. No explicit DevTools policy was established; Defender for Cloud Apps protection remains a possible cause. The normal background path uses the public WebView2 controller API and Windows SSO.

`scripts/diagnostics/repair-rcc-network.ps1` is a workstation-specific utility, not part of setup. RCC homes under `%LOCALAPPDATA%/robocorp` and `%LOCALAPPDATA%/actions` use the company CA profile with SSL verification enabled. `ssl-no-revoke` handles the observed Netskope certificates lacking revocation endpoints; Windows policy is unchanged. Backups stay under `.state/laptop-rcc-backup`.

## Local data

`.state` holds credentials, lifecycle control, runtime history, target identities, and verification evidence. `.runtime` is the launcher/development environment. `.package` is generated package input. None belongs in Git.

Downloads, profiles, SDK binaries, and durable send ledgers remain under `%LOCALAPPDATA%/OfficeActionServer`. Attachment downloads are bounded to 25 files/50 MiB per call, with sanitized names, actual saved-byte checks, and SHA256 receipts. Outlook scans are bounded to the newest 500 items.

The SDK installer verifies the pinned Microsoft NuGet package and extracted DLL hashes. It does not modify the installed WebView2 Runtime. Runtime versions remain `actions-runtime==1.0.4` and `actions-core==1.0.3`.
