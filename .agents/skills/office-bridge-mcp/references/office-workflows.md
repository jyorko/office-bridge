# Copilot, Outlook, and evidence packs

## Microsoft 365 Copilot Work IQ

- `teams_copilot_status()` checks readiness, Work IQ, and whether the hidden composer already has a draft.
- `teams_copilot_models()` reads enabled model choices; pass an exact enabled name to `teams_copilot_select_model(model)` to change the selection without asking a question.
- `teams_copilot_ask(question, request_id, model?)` submits one authorized question, up to 2,000 characters. Omit `model` to preserve the current selection.
- `teams_copilot_read(request_id)` reads the existing query; it never resubmits.

Use a unique request ID for an authorized question. Read by that same ID to collect a settled answer and observed citation links. `awaiting_response` is not a completed answer. `citations_complete: false` means the observed links are not a full export of the Sources panel. An uncertain or missing query must not be submitted again automatically; only continue a `not_submitted` request when the action confirms the original question is still unchanged and unsubmitted.

## Outlook

- Outlook uses a dedicated launcher-owned WebView2 profile. Require the host's exact configured-mailbox verification before reading or changing mail. A loaded app shell or account-picker option does not prove the active mailbox. Stop when `outlook_status.state` is `identity_unverified`. Never use classic Outlook, a normal browser profile, a keyboard, or the clipboard as a fallback.
- The controller enables audio before navigation. The normal launcher keeps it hidden. Show it only when the user asks to inspect the isolated verification window.
- `outlook_inbox_list(limit?, unread_only?)` and `outlook_search_inbox(query, limit?)` return conversation references. Outlook's list exposes conversation IDs. Pass a returned reference to `outlook_get_message(message)` to resolve an exact item ID. Do not use a conversation ID as a message ID.
- `MessageVersion.version_digest` binds the observed message content and flags. The adapter leaves `modified_at` null when it has no exact modification time. Use the version returned by `outlook_get_message` for any future mutation.
- The current live probe has verified mailbox identity and returned a bounded conversation list. It has not verified that the opened message pane exposes one exact item ID. `outlook_get_message` fails closed until the host proves that identity.
- Draft, reply, attachment, send, and archive handlers remain unavailable and return `outlook_operation_unavailable`. Do not call them until their WebView2 handlers pass synthetic tests and the read-only exact-message smoke succeeds.
## Evidence packs

`evidence_pack_create(pack)` packages caller-authored summary text, explicit source records (`reference`, `captured_at`, `content`), and coverage limitations. It does not fetch or independently verify sources. Packs allow up to 100 sources and 1 MiB total.

Only after explicit authorization of the destination, call `evidence_pack_send_devsy(pack_id, workspace, request_id)` with the exact returned pack ID and Devsy workspace ID. The delivery verifies hashes and returns a receipt. A completed identical request returns its receipt; uncertain delivery is blocked until the remote state is reconciled. Never repeat it automatically.
