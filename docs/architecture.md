# Package architecture

## Public actions

`src/outlook_actions.py`, `src/desktop_teams_actions.py`, `src/teams_actions.py`, `src/copilot_actions.py`, and `src/evidence_actions.py` contain typed entrypoints. Action names and input/output schemas remain the public contract. They import models and operations from `office_bridge`; action modules do not import one another.

`office_bridge/outlook/models.py` contains Outlook schemas. `office_bridge/teams/models.py` contains desktop and web Teams schemas. Shared boundary constraints live in `office_bridge/models.py`.

## Implementations

- `office_bridge/desktop.py` invokes fixed, bounded PowerShell JSON-file adapters. The Outlook bridge preserves EntryID/StoreID identity and version checks. Teams desktop navigation refuses to take foreground focus.
- `office_bridge/teams/service.py` owns background reads and durable send/edit/upload reservations. The existing group SQLite ledger and self-send ledger retain their locations and unknown-outcome behavior. Edit and upload records use separate tables in the group ledger.
- `office_bridge/teams/formatting.py` converts bounded Markdown to visible text, sanitized HTML, and expected formatting for browser verification.
- `office_bridge/teams/lookup.py` validates directory search results before exposing exact chat references.
- `office_bridge/teams/copilot.py` owns Copilot query contracts, private durable reservations, verified result caching, and model-choice validation.
- `office_bridge/teams/session.py` is the bounded named-pipe client for the launcher-owned Teams host.
- `office_bridge/teams/backend.py` validates WebView2 status and retains optional Chromium/Edge diagnostics.
- `office_bridge/runtime/supervisor.py` owns the hidden host lifecycle. `runtime/sdk.py` verifies the pinned SDK archive and DLL hashes.
- `office_bridge/native/` holds the PowerShell, C#, and browser assets next to the Python implementation that uses them.

## Persistent Teams session

The launcher starts one hidden WebView2 host before disposable Action Server workers. Its STA message loop keeps the controller alive between calls. The host owns the persistent-profile lock and serializes local named-pipe requests. A second hidden WebView2 controller in the same host handles protected rich-content composition without changing the Windows clipboard. It is not a separate HTTP or MCP server.

A third controller is created on first Copilot use. It keeps the Copilot conversation separate from chat navigation and accesses the embedded Microsoft 365 pane through the supported WebView2 frame API. Work IQ and the question's exact conversation turn are checked in that pane. The initial query call submits at most once; later reads wait for settled text and collect observed inline links. Model choices come from the current dropdown and can be selected before a question is entered. No normal browser profile or authentication tokens are copied.

The host uses the existing Windows account SSO and dedicated profile. Teams refreshes that profile's own authentication; normal browser cookies are not copied. Authentication challenges are reported rather than bypassed.

Workers can be replaced without replacing the browser. Each operation refreshes the host's JavaScript sources from disk, so JavaScript changes take effect without replacing the host. Changes to the native C# host require `scripts/restart_teams_session.py`; status reports `restart_required` until that host reload. The supervisor limits restarts, and stop-office requests graceful host shutdown before terminating the verified server tree.

All browser work is confined to the isolated session. Its containing window is never shown or activated. Popups and external app launches are blocked; the host checks for owned foreground interference. Browser-scoped input does not use the desktop keyboard or clipboard.

## Packaging and development

`package.yaml` declares worker dependencies, Python development dependencies, and development tasks. The official `action-server devenv` command provisions those tools in its datadir from `package.yaml`; there is no `requirements-dev.txt` or required `.runtime` environment. `robocorp-browser` resolves its compatible Playwright dependency.

`bootstrap.ps1` installs prerequisites, then delegates startup, read-only verification, and Codex/Copilot registration to `powershell/start-office.ps1`. Startup prefers port 8766 and falls back to the official Runtime's port-zero allocation. It publishes the actual URL only after the Runtime announces a bound endpoint and the listener is verified as a descendant of the recorded launcher. `.state/server.json` is the shared endpoint and instance record; clients and verifiers never fall back to a guessed URL. A startup lock prevents concurrent launches. Both modes use loopback without an API key; `-Dev` only enables reload. `office_bridge/runtime/local_state.py` keeps the expected work account independent of disposable checkout state. Application RCC environments use `%LOCALAPPDATA%/OfficeActionServer/rcc-actions` unless `ACTIONS_HOME` is explicitly set. Task commands must retain that environment choice so they use the intended certificate trust profile.

`scripts/package_layout.py` derives staged files from `src/` and `package.yaml`. It excludes generated caches and never enumerates `.runtime` or private state as package input. It removes retired staged sources and copies only changed files. Development watching covers `src/` recursively, including added and removed files.

The full schema snapshot is maintained with `python scripts/export_openapi.py`. Tests exercise behavior rather than depending on the old flat module layout.
