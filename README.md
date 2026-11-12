# Office Bridge

Local Outlook and Teams automation through the community Actions Runtime and its authenticated MCP endpoint. Outlook uses Windows COM. Background Teams authentication uses WebView2 with Windows account SSO and a separate persistent profile; its containing window is never shown or activated.

Requires Windows, classic Outlook, the Microsoft WebView2 Runtime, and `uv` on PATH. Desktop Teams and installed Edge are optional diagnostic routes. The self-send tools are restricted to Joshua's work account; this is a personal automation package.

Run these commands from this directory in PowerShell:

```powershell
./setup.ps1
./.runtime/Scripts/python.exe -m actions.server devenv task install-webview2
./start-office.ps1
./.runtime/Scripts/python.exe ./verify_mcp.py
./.runtime/Scripts/python.exe ./register_mcp.py
```

The authenticated MCP address is `http://127.0.0.1:8766/mcp`. The server name in Codex is `desktop-office`. Reload the MCP connection or start a new Codex session after registration. An existing session can retain its previous tool list.

Use an unlocked Windows session. Outlook COM attaches to Outlook or starts it when absent. Teams starts minimized from its registered installation when no Teams process is running. An existing meeting or an unavailable main view returns `app_not_ready` instead of starting a second instance. Cold app launch was checked with synthetic launch tests; existing live apps were not closed to test it.

Teams uses UI Automation text values and button invocation. It does not use simulated keys, mouse input, focus calls, or the clipboard. Reading the active chat works in the background. Switching chats can activate Teams, so background navigation returns `navigation_requires_foreground` before selecting anything. Open the target chat yourself, or use navigation while Teams is already foreground and idle. It refuses navigation or sending while you are editing in the foreground Teams window, and refuses to replace an existing composer draft. Results cover recognized loaded chats and currently loaded messages. Inbox listing and search inspect at most the newest 500 items.

The package exposes Outlook listing, search, message reads, reply drafts, draft submission, and archive operations. It also creates new mail drafts addressed to your own work account. Teams exposes chat listing, reading, and sending to your self-chat. Sending requires a separate authorized tool call. Drafts use plain text, reject attachments, and check the body, subject, recipients, and sender account before submission. `verify_mcp.py` performs only reads. `test_self_roundtrip.py` runs the specifically authorized email and Teams sends to yourself and records their outcomes to prevent automatic duplicate sends.

To stop the server, run `./stop-office.ps1`. To apply source changes, stop and start it. Startup stages the action modules and fixed PowerShell adapters in `.package`, then the official Actions Runtime imports `package.yaml` and builds its RCC environment. `.runtime` contains the pinned runtime host. It does not supply the managed action worker environment.

For local development, stop the server and run `./start-office.ps1 -Dev`. This synchronizes edits to the staged action sources and uses the runtime's `--auto-reload` option. It disables API-key authentication on `127.0.0.1:8766`. Start without `-Dev` to return to authenticated operation.

`outlook_download_attachments` saves attachments into `%LOCALAPPDATA%/OfficeActionServer/attachments` and returns their local paths and SHA256 hashes. Its `pdf_only` option skips inline signature images. It scans at most 500 attachment entries and saves at most 25 files or 50 MiB per call. It never opens or executes downloaded files.

`teams_web_status` defaults to `browser_engine="webview2"`. A fixed PowerShell adapter uses the public WebView2 controller API, requests Windows primary account SSO, and keeps its containing window unshown. The page renderer remains active so Teams can finish loading. The adapter checks the signed-in profile menu for the exact work account and blocks popups and external app launches. Its profile is `%LOCALAPPDATA%/OfficeActionServer/teams-webview2-profile`. Fresh managed MCP workers verified this background authentication path on this laptop without a sign-in prompt.

`teams_web_send_qa_chat` sends only to the exact `QA-ASSIST-VERINT-DEPLOY` chat. It checks the signed-in account, chat title, and empty composer before sending, then observes the message in the chat. It records a request ID and body digest outside the repository before the attempt; an uncertain outcome is never retried automatically. A live MCP call sent and observed a message in that group. General web chat reads and self-chat sends are not yet exposed as actions.

The SDK installer pins Microsoft's NuGet package and DLL hashes, installs only the required SDK files into `%LOCALAPPDATA%/OfficeActionServer/webview2-sdk`, and verifies cached files before reuse. It does not install or modify the system WebView2 Runtime. Windows and organization authentication requirements still apply; a new sign-in challenge is reported rather than bypassed.

`browser_engine="chromium"` and `"msedge"` remain isolated diagnostic routes using `robocorp-browser`. Provision matching Chromium with the `install-browser` development task. Those routes use separate profiles and do not share a normal browser's cookies.

Verify the default background path with `./.runtime/Scripts/python.exe verify_teams_web.py --dev` while the development server is running; omit `--dev` for the authenticated server. This read-only check performs two independent MCP sessions and requires the correct account, hidden host, and no owned-window focus. It records user foreground changes separately from automation interference.

The optional `teams_web_login.py` helper is for coordinated, visible Chromium or Edge diagnosis. It uses the same profile lock as MCP and stays open until its windows close. It is not part of the default WebView2 workflow. In the Edge route, `access_blocked` with `browser_attachment_denied` means the browser refused the Teams page's DevTools attachment. No explicit DevTools policy appeared on this laptop; Defender for Cloud Apps in-browser protection is a possible cause, but its governing control has not been confirmed.

Run the offline checks with:

```powershell
./.runtime/Scripts/python.exe -m pytest tests -q
```

Runtime versions are `actions-runtime==1.0.4` and `actions-core==1.0.3`. `package.yaml` pins worker dependencies. The older standalone Action Server 1.2.6 used different package names.

RCC profiles in both `%LOCALAPPDATA%/robocorp` and `%LOCALAPPDATA%/actions` use `gainwell-company-ca`. This profile exports the existing Windows trusted roots and enables SSL certificate verification. Its `ssl-no-revoke` setting is enabled because the Gainwell Netskope certificates observed on this laptop omit revocation endpoints. This setting applies to RCC; it does not change Windows certificate policy. Repairing those gateway-issued certificates themselves requires the network administrator. Previous laptop settings are backed up in `.state/laptop-rcc-backup`.

The server binds to loopback. Its key, local run history, and read-verification results are under `.state`, restricted to your Windows account and SYSTEM. Codex stores the bearer header in your restricted user configuration. Do not publish either location. Action Server can retain mail and chat content in local run history.

Git excludes runtime environments, server data, browser profiles, downloaded documents, keys, certificates, logs, and the case-specific Hyatt download helper. `repair-rcc-network.ps1` is an optional, workstation-specific utility; it is not run by setup.
