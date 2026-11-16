# Operations

## Server lifecycle

Run `./bootstrap.ps1 -Dev -TeamsAccount your.name@company.com` after cloning. It installs prerequisites, configures RCC trust, starts the server, verifies MCP and a Teams self-chat read, and registers Codex. The account is saved privately, so later starts can omit the flag. `-PrepareOnly` stops after prerequisite installation. `-SkipCodex` leaves Codex configuration alone. The default without `-Dev` requires a private API key and verifies that unauthenticated MCP requests are refused.

The server binds to `127.0.0.1:8766` and runs hidden under the current Windows account. It is not a Windows service and does not start at logon. After setup, use `powershell/start-office.ps1 -Dev` to enable official auto-reload without an API key. Omit `-Dev` for authenticated operation. Development mode does not create or require a key. `powershell/stop-office.ps1` closes the isolated Teams host and stops only the verified launcher tree. Start refuses an unrelated listener on the port.

Codex registration uses `$CODEX_HOME/config.toml`, or `~/.codex/config.toml` by default, and preserves other servers. Restart or reconnect the MCP client after first registration. Existing registration for another endpoint is preserved for inspection rather than overwritten.

Python action changes reload automatically in development. The host refreshes JavaScript between operations; an operation stops if its script bundle changes during execution. After changing the native C# host, run `action-server devenv --datadir .state/tasks task reload-teams`, then `action-server devenv --datadir .state/tasks task verify-teams-dev`. This reloads the isolated Teams host while the server UI stays available. The supervisor limits starts to three per five minutes; if recovery stops, use the normal server stop/start commands. Check `teams_web_status.restart_required` before sends.

## Copilot Work IQ

`teams_copilot_status` opens or checks a dedicated hidden Copilot pane in the launcher-owned Teams session. The host verifies the configured Teams account through Windows SSO and verifies the embedded Microsoft 365 Copilot surface and enabled Work IQ switch. An existing Copilot draft is preserved. This pane is separate from chat navigation and survives disposable Action Server workers.

`teams_copilot_ask(question, request_id)` submits one explicitly authorized question, up to 2,000 characters, to the default Copilot experience. It does not choose another agent, switch Work IQ on, open source links, or confirm suggested actions. A durable private record is written before submission. Reusing a request ID for another question is refused; a completed or known submitted request returns its existing receipt. An uncertain submission is blocked from automatic replay.

`teams_copilot_models` reads the current model dropdown and its enabled choices, then closes the menu without changing the selection. Pass an exact returned name as the optional `model` argument to `teams_copilot_ask`. The host selects that choice before entering the question and requires Work IQ to remain enabled. Omitting `model` preserves the existing selection. Model names are discovered from the current tenant UI, and a request ID is bound to both its question and requested model.

`teams_copilot_select_model(model)` changes the selected dropdown choice without submitting a question and verifies the selection from the resulting menu. Existing drafts and unfinished queries block a change to another model.

`teams_copilot_read(request_id)` reads that existing request without resubmitting. `awaiting_response` is not a completed answer. Completion requires the exact question in the observed conversation turn, a response with its completion controls, no active stop/busy indicator, and stable response text. The result contains up to 32,000 answer characters and 50 deduplicated HTTP(S) links observed inside the response. `citations_complete: false` means these are inline links, not an exhaustive export of the Sources panel. Copilot's factual claims still need assessment against the cited records.

A `not_submitted` result means the current pane proves the original question was never clicked and its draft still matches. A subsequent explicit ask using that same question and request ID may continue it. Changed drafts and unknown outcomes remain blocked. Only one unfinished query is permitted in the pane. Captured completed results are cached under `%LOCALAPPDATA%/OfficeActionServer/copilot-queries` and remain readable after a host restart; an uncaptured pending query may become unavailable after navigation or restart and must not be silently resubmitted. Keep these private records recoverable alongside send ledgers.

Run `action-server devenv --datadir .state/tasks task test-copilot` for isolated guard and recovery tests. These tests never contact Microsoft or submit a live question. Use a separately authorized question for live verification.

## Evidence packs and Devsy delivery

`evidence_pack_create` accepts a typed pack with a title, caller-authored Markdown summary, explicit source records (`reference`, `captured_at`, `content`), and coverage limitations. It creates a content-addressed ZIP under `%LOCALAPPDATA%/OfficeActionServer/evidence-packs`. The archive contains `README.md`, `evidence.json`, and `manifest.json`; the summary is labelled as caller-authored. Packs are bounded to 100 source records and 1 MiB total. The action does not fetch additional sources automatically.

After the user authorizes a destination, call `evidence_pack_send_devsy` with the returned pack ID, exact Devsy workspace ID, and a unique request ID. The installed Devsy CLI must list that workspace and support `workspace ssh --command`; the remote workspace must run Linux with Python 3. Delivery uses stdin and a fixed receiver, disables SSH agent forwarding and helper services, and writes inside the selected container at `/workspaces/<workspace>/office-bridge-evidence/<pack_id>/`. The receiver verifies its current workspace directory and adds a local Git exclusion before writing evidence into a repository. That directory contains the three extracted files and `pack.zip`, with hashes checked before a verified receipt is returned. Existing different content is refused.

Delivery records stay beside local packs in `deliveries/`. An identical completed request returns its recorded receipt; an uncertain request is blocked until its remote state is reconciled. Do not automatically repeat an uncertain transfer. Devsy MCP currently has no dedicated file-transfer tool; Office Bridge uses the installed CLI's SSH route. Run `action-server devenv --datadir .state/tasks task test-evidence` for the isolated tests.

## Teams configuration and behavior

The dedicated profile is `%LOCALAPPDATA%/OfficeActionServer/teams-webview2-profile`. The expected account is saved in `config/settings.json` under that application data directory. There are no hardcoded people or chat names in the action interface.

A fresh Windows installation needs no old WebView2 profile or DLLs. Bootstrap downloads and verifies the SDK, detects or installs the Evergreen Runtime, and the tracked native code creates a new profile using Windows work-account SSO. Bootstrap verifies the exact account before declaring success. Organization sign-in requirements can still require user interaction on a new device; old cookies are not part of the source code.

`teams_web_find_chats` searches the Teams directory and returns bounded, verified exact chat references. `teams_web_list_chats` returns references from the currently loaded sidebar, optionally filtered by exact name. Pass a `{chat_id, title}` reference to `teams_web_read_chat`, `teams_web_send_chat`, or `teams_web_edit_message`. Actions verify the exact sidebar row and actual chat header. The same interface handles self chats and groups. Duplicate names have distinct IDs; never choose a recipient from its name alone.

Reads return bounded, currently loaded history. Sends check the configured account, conversation ID, title, and composer, reserve a request ID durably, click once, and observe a settled message. Never automatically retry an uncertain send. Navigation waits for the selected Chat workspace to hydrate before checking composers, and stops if it finds an existing draft. The separate hidden WebView2 controller used for rich content shares the isolated profile; it does not expose a window or use the Windows clipboard.

Send confirmation correlates a settled message ID, timestamp, and exact body after the single controlled click. It does not independently verify an immutable sender ID. Run installation checks without sending messages.

`teams_web_send_chat` and `teams_web_edit_message` accept `body_format="plain"`, the default, or `body_format="markdown"`. Supported Markdown includes paragraphs, bold, italic, inline code, lists, and HTTP(S) links. Raw HTML is escaped; images and unsupported block formats are refused. Input is limited to 2,000 characters. The browser verifies the rendered text and formatting before committing the change. Markdown crosses the host boundary as bounded text nodes with an allowlist of tags and attributes; the host builds DOM nodes and never inserts untrusted HTML through `innerHTML`. Rich insertion uses browser-local clipboard events and never changes the Windows clipboard.

To edit an existing message, pass its exact `message_id`, the same chat reference, and its current `expected_text` from a read. The action verifies that the signed-in account owns the message and that its text has not changed. It saves that message once and verifies the same message ID afterward. Each edit reserves a `request_id` in the private ledger before attempting a change. Repeating a completed request returns its receipt; an uncertain edit remains blocked for inspection. Editing requires explicit user authorization, like sending.

Editing preserves an unchanged mention at the start of the existing message. Changing that mention or editing a message with mentions elsewhere is refused. Creating notification mentions is not part of the Markdown interface.

The optional desktop Teams tools use Windows UI Automation. Reading the current chat can work in the background, but navigation that would activate Teams is refused. Do not relax that protection to make a test pass.

## Verification and API reference

```powershell
action-server devenv --datadir .state/tasks task test
action-server devenv --datadir .state/tasks task lint
action-server devenv --datadir .state/tasks task format-check
action-server devenv --datadir .state/tasks task verify-teams-dev
action-server devenv --datadir .state/tasks task export-openapi
```

Run task commands with the intended RCC home. Bootstrap defaults `ACTIONS_HOME` to `%LOCALAPPDATA%/OfficeActionServer/rcc-actions`; preserve an explicitly configured override. The selected home contains the TLS trust profile used when devenv builds task environments.

The Teams verifier uses fresh MCP sessions, verifies the exact work account and hidden host, and checks session continuity. It never sends messages. `scripts/bootstrap_check.py` additionally reads the self-chat without recording message contents. `scripts/register_mcp.py` accepts that verification, with `--dev` for development. The separate `scripts/verify_mcp.py` expects authentication and checks Outlook and desktop Teams reads.

Completed send and edit receipts and audits remain private. A cold installation check must not replay a historical request.

## Optional diagnostics

`scripts/diagnostics/teams_login.py` is a coordinated visible Chromium/Edge sign-in helper, not the normal WebView2 path. The matching Chromium binary can be installed with the package's `install-browser` development task. Chromium, installed Edge, and WebView2 use separate profiles.

Installed Edge previously refused DevTools attachment to the Teams page. No explicit DevTools policy was established; Defender for Cloud Apps protection remains a possible cause. The normal background path uses the public WebView2 controller API and Windows SSO.

`powershell/diagnostics/repair-rcc-network.ps1` is the older workstation repair utility. Normal bootstrap configures the application's own RCC home at `%LOCALAPPDATA%/OfficeActionServer/rcc-actions`, honoring an explicit `ACTIONS_HOME` override. It merges Windows trusted roots into the active profile while retaining other settings and leaves certificate verification enabled. `ssl-no-revoke` handles the observed Netskope certificates lacking revocation endpoints. Windows policy and shared RCC homes are unchanged. Private exported profiles stay under `.state`.

## Local data

`.state` holds the checkout's credential copy, lifecycle control, runtime history, and verification evidence. `.runtime` is an ignored legacy disposable environment only; it is not required. `.package` is generated package input. Pytest, Ruff, and Python caches are disposable. None contains implementation that must be recovered before installing from Git.

The device API key and configured account live in `%LOCALAPPDATA%/OfficeActionServer/config`. Startup migrates the old checkout key without rotating it and restores the checkout copy after a reclone. Conflicting keys stop startup without overwriting either key. Development mode does not create or require a key.

Downloads, profiles, SDK binaries, and durable send ledgers remain under `%LOCALAPPDATA%/OfficeActionServer`. Attachment downloads are bounded to 25 files/50 MiB per call, with sanitized names, actual saved-byte checks, and SHA256 receipts. Outlook scans are bounded to the newest 500 items.

Bootstrap can recreate software and an empty profile after that directory is removed. It cannot recreate downloaded receipts, historic send ledgers, or a lost API key. Preserve those private records before a reset. An empty install verifies reads only; do not replay old send requests after removing their ledger.

The SDK installer verifies the pinned Microsoft NuGet package and extracted DLL hashes. The bootstrap detects the installed WebView2 Runtime and installs the official Evergreen Runtime only when missing. Runtime versions are `actions-runtime==1.1.1` and `actions-core==1.1.0`. The Windows release executable is pinned to the same Runtime version with SHA-256 checks. Work Items `0.5.0` is a transitive Runtime dependency; Office Bridge does not use its workflows or storage.

Verification uses the Runtime's stateless MCP `2026-07-28` protocol with `server/discover` and per-request routing metadata.
