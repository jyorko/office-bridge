# Copilot, Outlook, and evidence packs

## Microsoft 365 Copilot Work IQ

- `teams_copilot_status()` checks readiness, Work IQ, and whether the hidden composer already has a draft.
- `teams_copilot_models()` reads enabled model choices; pass an exact enabled name to `teams_copilot_select_model(model)` to change the selection without asking a question.
- `teams_copilot_ask(question, request_id, model?)` submits one authorized question, up to 2,000 characters. Omit `model` to preserve the current selection.
- `teams_copilot_read(request_id)` reads the existing query; it never resubmits.

Use a unique request ID for an authorized question. Read by that same ID to collect a settled answer and observed citation links. `awaiting_response` is not a completed answer. `citations_complete: false` means the observed links are not a full export of the Sources panel. An uncertain or missing query must not be submitted again automatically; only continue a `not_submitted` request when the action confirms the original question is still unchanged and unsubmitted.

## Outlook

- `outlook_inbox_list(limit?, unread_only?)` lists recent mail; `outlook_search_inbox(query, limit?)` applies literal subject/sender and date/unread filters. `outlook_get_message(message)` reads a selected message. Carry the exact `MessageRef` and `MessageVersion` from results.
- `outlook_download_attachments(entry_id, store_id, pdf_only?)` saves bounded attachments without opening them.
- `outlook_create_reply_draft(source, body_text, expected_source, reply_all?)` creates a reply draft after checking the source version. Set `reply_all: true` to include the source message's other recipients, then inspect the returned recipients before sending. `outlook_create_mail_draft(recipient_email, subject, body_text)` is restricted to the signed-in user's company address. Neither sends.
- `outlook_send_draft(draft, expected_digest)` submits an inspected draft. Require explicit authorization and pass the exact returned digest.
- `outlook_archive(message, expected_source)` moves unchanged mail to its existing Archive folder. Require authorization and use the current version.

Draft creation, send, archive, and attachment download have different effects; never treat a draft receipt as a sent message. If send or archive state is unclear, inspect Outlook before acting again.

## Evidence packs

`evidence_pack_create(pack)` packages caller-authored summary text, explicit source records (`reference`, `captured_at`, `content`), and coverage limitations. It does not fetch or independently verify sources. Packs allow up to 100 sources and 1 MiB total.

Only after explicit authorization of the destination, call `evidence_pack_send_devsy(pack_id, workspace, request_id)` with the exact returned pack ID and Devsy workspace ID. The delivery verifies hashes and returns a receipt. A completed identical request returns its receipt; uncertain delivery is blocked until the remote state is reconciled. Never repeat it automatically.
