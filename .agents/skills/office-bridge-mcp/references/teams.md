# Teams actions

Use `teams_web_status` to check the isolated background session before troubleshooting. Its `state` can be `authenticated`, `needs_interactive_login`, `access_blocked`, `loading`, or `failed`; inspect `restart_required` when present. Authentication must identify the configured work account.

## Find and read a chat

- `teams_web_list_chats(name?, limit?)` returns verified references from the currently loaded chat list. `name` is an exact title filter.
- `teams_web_find_chats(query, limit?)` searches the Teams directory and returns verified references.
- `teams_web_read_chat(chat, limit?)` reads bounded history for an exact `{chat_id, title}` reference.

Preserve the returned `chat_id` and `title` together. If search returns duplicates or the intended destination is not clear, ask the user which exact chat to use. A bounded history may contain fewer messages than requested.

## Send or edit

`teams_web_send_chat(chat, body, request_id, body_format?, mentions?)` sends one message to the referenced chat. `body_format` is `plain` by default or supported `markdown`. Require authorization for the exact destination and content. The action reserves the request ID before its single click and verifies a settled message. If the result is uncertain, inspect the chat; do not retry.

The optional `mentions` array is a structured person-mention plan. Each entry has a unique lowercase `key`, an exact `display_label`, and a recipient's explicit email/UPN. Put `{{mention:key}}` exactly once in ordinary paragraph or list text. Placeholders in code, links, emphasis, or strong formatting are refused. A non-empty plan is rejected before reservation unless `teams_web_status` reports the loaded host capability `person_mentions_v1`; the current host reports no such capability. The native host rejects unknown mention arguments and its durable claim digest does not yet bind a mention plan. Keep capability advertisement disabled until native plan validation and digest binding, stable identity resolution, picker handling, and settled entity verification are implemented. Do not treat literal `@Name` text as a mention. A settled receipt may report observed mention entities, but notification delivery is not verified.

`teams_web_inspect_mention_evidence(chat, message_id)` reads one existing settled message in the hidden auxiliary view. It returns bounded DOM structure and hashed attribute-value shapes; it does not return message text, names, email addresses, or raw IDs, and it preserves the primary composer. It does not open a picker or resolve a person.

`teams_web_edit_message(chat, message_id, expected_text, body, request_id, body_format?)` edits an existing message. Use the exact message ID and current text from a fresh read. It verifies chat identity, ownership, and unchanged text before saving. Mentions outside an unchanged leading mention are unsupported. Editing requires authorization; an uncertain result must be reconciled without replay.

Supported Markdown includes paragraphs, bold, italic, inline code, lists, and HTTP(S) links. Raw HTML is escaped; images and unsupported block formats are refused. Neither rich insertion nor other Teams actions use the Windows clipboard.

## Links and reactions

- `teams_web_message_link(chat, message_id)` returns the exact message's deep link.
- `teams_web_message_reactions(chat, message_id, expected_text)` reads the current supported reaction controls and returns `content_text`.
- `teams_web_react_message(chat, message_id, expected_text, reaction, request_id)` adds one exact enabled reaction after authorization. It does not toggle off an existing own reaction.

Use the message ID and exact text from a current read. For a work task message, `content_text` excludes reaction summaries and is the expected message body for work tracking. A reaction mutation requires authorization. Do not use an ordinary reaction to represent work tracking; use the dedicated opt-in work actions in [work tracking](work-tracking.md).

## Optional desktop Teams reads

`teams_list_chats(limit?)` and `teams_read_chat(chat, limit?)` use Windows UI Automation against the desktop Teams window. Prefer the isolated web actions for hidden background work. Desktop reading can change the selected visible chat; do not navigate if doing so would activate or disturb the user's session.
