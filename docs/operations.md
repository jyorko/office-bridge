# Operations

## Server lifecycle

Run `./bootstrap.ps1 -Dev` after cloning. It installs prerequisites, configures RCC trust, starts the server, verifies MCP and a Teams self-chat read, and registers Codex and Copilot. Bootstrap reuses private account configuration; otherwise, it detects the signed-in Windows work account with `whoami /upn`. Pass `-TeamsAccount your.name@company.com` to override the account or when Windows cannot identify it. An explicit argument or `OFFICE_BRIDGE_TEAMS_ACCOUNT` takes precedence over saved configuration and detection. The hidden Teams host still verifies the exact account. `-PrepareOnly` skips account detection and stops after prerequisite installation. `-SkipCodex` and `-SkipCopilot` leave the corresponding client alone. Neither mode uses an API key; `-Dev` only enables auto-reload.

The server binds to `127.0.0.1` and runs hidden under the current Windows account. It is not a Windows service and does not start at logon. Startup tries port 8766; if it is occupied, it passes `--port 0` to the official Runtime so Windows chooses a free port. `-Port 0` always requests that behavior. A bind collision during startup permits one retry with port zero, only after the failed launcher exits. Startup records the Runtime-announced URL after verifying its listener belongs to the launcher tree. `.state/server.json` holds `mcp_url` and `instance`; all verifiers and registration use it. Missing endpoint state is an error, never permission to contact an unrelated default-port service. `powershell/stop-office.ps1` closes the isolated Teams host and stops only the verified launcher tree.

Each start, including reuse of a running server, verifies reads and refreshes both client entries. Codex uses `$CODEX_HOME/config.toml` or `~/.codex/config.toml`; Copilot uses `$COPILOT_HOME/mcp-config.json` or `~/.copilot/mcp-config.json`, following the [native Copilot configuration format](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#mcp-server-configuration). Both files are parsed before any write. Registration removes `desktop-office`, verifies its absence, writes the new endpoint without authorization headers, and reads it back. Other entries and settings are preserved; Codex comments are retained. Atomic writes reject concurrent edits and retain no backups. Verification evidence must match the current endpoint, reload mode, and server instance. Restart or reconnect an already-running client if it retains an older configuration in memory.

The launcher runs two managed tasks: `serve` or `serve-dev`, followed by the matching `complete-startup` task. The serve task prepares private account configuration; completion verifies Teams before registering clients. All dependencies are pinned in the base dependency set, so the launcher, workers, tests, and tools share one RCC environment. Repeating startup reuses it. Bootstrap configures Windows certificate trust directly with PowerShell and the pinned RCC CLI before the first environment build.

Python action changes reload automatically in development. Each Teams host loads an immutable native/JavaScript code snapshot in its private application directory. Editing repository sources does not invalidate operational reads. After changing native C# or JavaScript, run `action-server devenv --datadir .state/tasks task reload-teams`, then `action-server devenv --datadir .state/tasks task verify-teams-dev`. This reloads the isolated Teams host while the server UI stays available. The supervisor limits starts to three per five minutes; if recovery stops, use the normal server stop/start commands. Check `teams_web_status.restart_required` before sends; unexpected changes to the loaded private snapshot still stop operations.

## Outlook Web session

The Outlook controller is a launcher-owned WebView2 host with its own persistent profile at `%LOCALAPPDATA%/OfficeActionServer/outlook-webview2-profile`. It reads the expected mailbox from the private application configuration and enables Windows primary-account SSO. The initial `https://outlook.office.com/mail/` navigation can redirect to `outlook.cloud.microsoft`; both hosts are explicitly allowed by the controller. Do not copy cookies or profile data from the normal browser.

The Outlook app shell loading is not proof of mailbox authentication. The host reports `authenticated` only when the active Outlook account menu contains exactly the configured mailbox and a sign-out control. A missing account menu, an account-picker option, or a successful page navigation without that evidence remains `identity_unverified`; stop rather than falling back to classic Outlook.

The launcher does not pass `-Visible` to the Outlook bootstrap, so normal operation stays hidden. For an explicitly requested visual check, `src/office_bridge/native/outlook/bootstrap.ps1 -Visible` shows only the dedicated Outlook profile. It does not use keyboard input, clipboard, or the normal Outlook/browser window. The same profile lock prevents a second Outlook controller from using the profile concurrently. `CoreWebView2.IsMuted` is set to `false` before navigation so Outlook audio can play, and `outlook_status.audio_muted` reports the live mute state.

After changing Outlook's native C# host, run `action-server devenv --datadir .state/tasks task reload-outlook`. The task sends the configured shutdown token through the current named pipe and waits for the launcher supervisor to start a new generation. Do not restart an Outlook process by name or use a pipe from another profile.

`outlook_inbox_list` and `outlook_search_inbox` return conversation references because Outlook's list exposes conversation IDs. Pass one to `outlook_get_message` to resolve an exact message item ID. The detail reference includes both the item ID and its conversation ID. `MessageVersion.version_digest` binds the observed message fields and body. The adapter leaves `modified_at` null rather than deriving it from the received time.

The live hidden host has verified the configured mailbox and returned a bounded inbox list. The current probe has not yet proved an exact item ID from an opened message. `outlook_get_message` fails closed until the loaded host proves that ID. The draft, reply, attachment, send, and archive handlers remain unavailable and return `outlook_operation_unavailable`. Do not treat those actions as ready until the WebView2 handlers pass synthetic behavior tests and the read-only live message check succeeds.

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

`teams_web_upload_readiness(chat)` checks the exact chat's hidden composer and file input without changing the draft. `teams_web_upload_files(chat, file_paths, request_id)` accepts 1–10 absolute local regular-file paths, with a 25 MiB per-file and 50 MiB total limit. Names must be unique and safe; symbolic links, reparse-point files, UNC paths, and device paths are refused. Paths are hashed before the request and are not kept in the ledger. Existing text, attachments, or an unavailable file input blocks the upload.

An upload reserves its request ID before assigning files through the hidden WebView2 file input. It waits for all filenames to appear in the composer with upload progress settled, clicks Send once, and returns only after it observes one new message in the exact chat with the expected filenames, sender, message ID, and timestamp. Repeating a settled request returns its receipt. `teams_web_upload_status(chat, request_id)` never clicks or resubmits; it recovers a durable receipt when present and otherwise returns `outcome_unknown`, which requires manual inspection of that exact chat before any further action. Uploading requires explicit authorization, like sending.

Editing preserves an unchanged mention at the start of the existing message. Changing that mention or editing a message with mentions elsewhere is refused.

`teams_web_send_chat` also accepts an optional `mentions` array for plain-text messages. Each entry has a unique lowercase `key`, the exact picker `display_label`, and an explicit `recipient.email`; include `{{mention:key}}` exactly once in the body. Plan entries must follow placeholder order. Python and native request digests bind the mention plan to the body and destination; an omitted or empty plan leaves existing request identities and payloads unchanged. Notification delivery is never reported as verified.

The authenticated hidden host advertises `person_mentions_v1`. It resolves each recipient through the real picker using the exact email/UPN, binds the candidate's organizational MRI to the inserted chip, and checks the draft before its single click. A receipt verifies the new own settled message, the expected body, and each mention. Teams replaces the chip MRI with a message-local ordinal after posting; verification binds the ordered settled entities and labels to the retained, MRI-verified insertion plan. A pre-click failure clears only an unchanged draft owned by this operation, using hidden editor commands so its model and DOM agree. Existing or changed composers are preserved. Unknown outcomes retain their reservations and must be reconciled without replay. Literal `@Name` is ordinary text. `teams_web_inspect_mention_evidence(chat, message_id)` locates an existing settled mention and returns only bounded structure and hashed value shapes. It does not resolve identities or open the picker.

The optional desktop Teams tools use Windows UI Automation. Reading the current chat can work in the background, but navigation that would activate Teams is refused. Do not relax that protection to make a test pass.

## Eyes work tracking

Tracking is disabled until the user authorizes a session and exact chats through `teams_work_tracking_configure(session_id, chats, enabled=true)`. On enrolled task messages, Eyes is reserved for automation. An existing Eyes reaction is never claimed or removed. The same signed-in account is used by humans and agents; a manual remove-and-re-add between observations cannot be distinguished reliably, so avoid manually changing Eyes on enrolled messages. Detected interference stops automatic changes.

Use `teams_web_message_reactions` to obtain `content_text`, which excludes reaction summaries. Call `teams_work_begin` with a stable session/work ID, exact chat/message identity, and that expected content when meaningful investigation starts. Passive reading, queued requests, and unrelated messages do not need a marker. The action records a durable claim even when Eyes is unavailable; a warning lets the agent continue its authorized work without a reaction.

Call `teams_work_finish` with `completed`, `paused`, `cancelled`, or `failed` when the claim ends. An interim reply does not end continuing investigation. Only the last active claim can clear an owned marker. `completed` means the requested result was verified; it does not add a completion emoji or send a reply. Configure scopes once rather than asking again for each authorized transition. Disabling a session blocks new claims while retaining permission to clean its existing owned markers.

`teams_work_status` reconciles durable records and current observations without clicking. Claims survive disposable workers and crashes and never expire automatically. Finish known abandoned claims explicitly with their actual outcome. A crash before a cleanup reservation can be reconciled by finishing that same ended claim again. Pending/uncertain transitions block every new request ID; they must not be retried. Changed task content or externally changed Eyes stops cleanup. Cached receipts describe historical operations, not current UI state.

Private tracking data is stored under `%LOCALAPPDATA%/OfficeActionServer/teams-web-sends/work-tracking`, partitioned by configured account and exact message. Each message has a coordinator lock, claims, baseline content, ownership, and durably reserved transition generations. Account configuration changes require the managed server to restart. Full user-requested resets remove these records with the rest of device state.

This is an MCP lifecycle agents invoke for assigned work, not a chat monitor or background lease cleaner. No existing conversation is enrolled during installation or verification. General reaction removal is not exposed; the lifecycle only removes Eyes it owns.

## Verification and API reference

```powershell
action-server devenv --datadir .state/tasks task test
action-server devenv --datadir .state/tasks task lint
action-server devenv --datadir .state/tasks task format-check
action-server devenv --datadir .state/tasks task verify-teams-dev
action-server devenv --datadir .state/tasks task export-openapi
```

Run task commands with the intended RCC home. Bootstrap defaults `ACTIONS_HOME` to `%LOCALAPPDATA%/OfficeActionServer/rcc-actions`; preserve an explicitly configured override. The selected home contains the TLS trust profile used when devenv builds task environments.

The Teams verifier uses fresh MCP sessions, verifies the exact work account and hidden host, and checks session continuity. It never sends messages. `scripts/bootstrap_check.py` additionally reads the self-chat without recording message contents. `scripts/register_mcp.py` requires that verification for the current server instance. The separate `scripts/verify_mcp.py` checks Outlook and desktop Teams reads.

Completed send and edit receipts and audits remain private. A cold installation check must not replay a historical request.

## Optional diagnostics

`scripts/diagnostics/teams_login.py` is a coordinated visible Chromium/Edge sign-in helper, not the normal WebView2 path. The matching Chromium binary can be installed with the package's `install-browser` development task. Chromium, installed Edge, and WebView2 use separate profiles.

Installed Edge previously refused DevTools attachment to the Teams page. No explicit DevTools policy was established; Defender for Cloud Apps protection remains a possible cause. The normal background path uses the public WebView2 controller API and Windows SSO.

`powershell/diagnostics/repair-rcc-network.ps1` is the older workstation repair utility. Normal bootstrap configures the application's own RCC home at `%LOCALAPPDATA%/OfficeActionServer/rcc-actions`, honoring an explicit `ACTIONS_HOME` override. It merges Windows trusted roots into the active profile while retaining other settings and leaves certificate verification enabled. `ssl-no-revoke` handles the observed Netskope certificates lacking revocation endpoints. Windows policy and shared RCC homes are unchanged. Private exported profiles stay under `.state`.

## Local data

`.state` holds lifecycle control, the actual endpoint, runtime history, and verification evidence. `.runtime` is an ignored legacy disposable environment only; it is not required. `.package` is generated package input. Pytest, Ruff, and Python caches are disposable. None contains implementation that must be recovered before installing from Git.

The configured account lives in `%LOCALAPPDATA%/OfficeActionServer/config`. Startup does not create an API key and removes obsolete key files from the device config and checkout state. Both MCP entries omit authorization headers.

Downloads, profiles, SDK binaries, and durable send ledgers remain under `%LOCALAPPDATA%/OfficeActionServer`. Attachment downloads are bounded to 25 files/50 MiB per call, with sanitized names, actual saved-byte checks, and SHA256 receipts. Outlook scans are bounded to the newest 500 items.

Bootstrap can recreate software and an empty profile after that directory is removed. It cannot recreate downloaded receipts or historic send ledgers. A full reset explicitly requested without backups removes those records; do not preserve or restore them against that request. An empty install verifies reads only; do not replay old send requests after removing their ledger.

The SDK installer verifies the pinned Microsoft NuGet package and extracted DLL hashes. The bootstrap detects the installed WebView2 Runtime and installs the official Evergreen Runtime only when missing. Runtime versions are `actions-runtime==1.1.1` and `actions-core==1.1.0`. The Windows release executable is pinned to the same Runtime version with SHA-256 checks. Work Items `0.5.0` is a transitive Runtime dependency; Office Bridge does not use its workflows or storage.

Verification uses the Runtime's stateless MCP `2026-07-28` protocol with `server/discover` and per-request routing metadata.
