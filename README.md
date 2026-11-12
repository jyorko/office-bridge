# Office Bridge

Local Outlook and Teams automation through the community Actions Runtime and its authenticated MCP endpoint. Windows desktop controls handle Outlook and the active Teams chat. A separate persistent Edge profile supports the Teams web status probe.

Requires Windows, classic Outlook, Microsoft Teams or installed Edge, and `uv` on PATH. The self-send tools are currently restricted to Joshua's work account; this is a personal automation package.

Run these commands from this directory in PowerShell:

```powershell
./setup.ps1
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

`teams_web_status` uses `from actions import action` and `from robocorp import browser`. It configures installed Edge with `browser_engine="msedge"`, `headless=True`, and `%LOCALAPPDATA%/OfficeActionServer/teams-web-profile` as its persistent context directory. It reports authentication state; web chat reads and sends are not implemented yet. The dedicated profile does not inherit an existing Edge profile's login. Run `teams_web_login.py` in the runtime environment for a coordinated, visible first sign-in. Normal browser windows stay separate.

The sign-in helper stays open until you close its isolated browser windows. Windows account sign-in may complete automatically. A saved login does not guarantee automation access: `access_blocked` with `browser_attachment_denied` means Edge refused the Teams page's DevTools attachment. This restriction was observed on this laptop; its governing setting has not been identified.

Run the offline checks with:

```powershell
./.runtime/Scripts/python.exe -m pytest tests -q
```

Runtime versions are `actions-runtime==1.0.4` and `actions-core==1.0.3`. `package.yaml` pins worker dependencies. The older standalone Action Server 1.2.6 used different package names.

RCC profiles in both `%LOCALAPPDATA%/robocorp` and `%LOCALAPPDATA%/actions` use `gainwell-company-ca`. This profile exports the existing Windows trusted roots and enables SSL certificate verification. Its `ssl-no-revoke` setting is enabled because the Gainwell Netskope certificates observed on this laptop omit revocation endpoints. This setting applies to RCC; it does not change Windows certificate policy. Repairing those gateway-issued certificates themselves requires the network administrator. Previous laptop settings are backed up in `.state/laptop-rcc-backup`.

The server binds to loopback. Its key, local run history, and read-verification results are under `.state`, restricted to your Windows account and SYSTEM. Codex stores the bearer header in your restricted user configuration. Do not publish either location. Action Server can retain mail and chat content in local run history.

Git excludes runtime environments, server data, browser profiles, downloaded documents, keys, certificates, logs, and the case-specific Hyatt download helper. `repair-rcc-network.ps1` is an optional, workstation-specific utility; it is not run by setup.
