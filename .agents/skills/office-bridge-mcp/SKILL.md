---
name: office-bridge-mcp
description: Use the repo's Office Bridge MCP server for Teams, Microsoft 365 Copilot, Outlook, or evidence-pack actions. Covers live tool discovery, exact identities, and safe handling of writes and uncertain outcomes.
---

# Office Bridge MCP

Use the registered `desktop-office` MCP connection and its live action catalog. Office Bridge runs the community Actions Runtime and Actions Core; do not replace them with a custom server or assume a tool name or schema from memory.

If the client is disconnected, read `.state/server.json` and use its recorded `mcp_url`. The endpoint is loopback and ends in `/mcp`; the full API schema is at the same origin's `/openapi.json`. The port can change. Do not guess it or start another server over a recorded one.

Discover the live action catalog with paginated MCP `tools/list` before choosing an action. `server/discover` describes protocol support, not the tool catalog. Use the returned names and input schemas as the call contract; consult the API schema and repo source when behavior needs clarification. Stop if the requested action is absent or its identity/schema is ambiguous.

For a user-installed copy, read `references/installation.md` to locate the owning checkout. Resolve state and lifecycle scripts there, rather than in an unrelated current working directory.

For Teams, select a chat using a verified `{chat_id, title}` reference from list or search results. A title alone is not an identity; resolve duplicate or missing matches before reading or writing. The Teams host is isolated and hidden: do not use the user's keyboard, clipboard, desktop focus, normal browser profile, or Teams window.

Get explicit user authorization for consequential actions such as sending or editing a Teams message, reacting, submitting a Copilot question, sending an Outlook draft, archiving mail, or delivering an evidence pack. Use a fresh request ID wherever the action requires one. If an operation's outcome is uncertain, inspect or reconcile its existing record and destination; never replay it automatically under the same or a new ID.

Read the relevant guide only when using that area:

- [Teams actions](references/teams.md) for chat lookup, read, send, edit, links, and reactions.
- [Work tracking](references/work-tracking.md) for opt-in Eyes claims on assigned task messages.
- [Copilot, Outlook, and evidence packs](references/office-workflows.md) for those action families.
- [Message presentation](references/presentation.md) for Outlook and Teams views in native MCP Apps, inline in T3, or in the isolated window for standalone clients.
