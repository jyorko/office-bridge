# Outlook web acceptance

The Windows host compiles and verifies the configured account in its hidden WebView2 session. Inbox listing and literal search work against the live mailbox.

Selected-message reads remain blocked. The host has observed Outlook service, REST, and batch responses, but the consumer's two read-only reproducers have not returned a verified exact message. A successful inbox list does not prove the reader works.

The native draft, reply, send, archive, and attachment handlers are implemented. Their public routes remain disabled pending live acceptance. Canvas reply controls follow the published backend capability.

The reader excludes icon-font glyphs from inbox subject extraction. Response parsing handles WCF type markers and supported REST field names. Host restart checks the loaded source hash to prevent testing an older native build.

Before enabling writes or merging:

1. Run `action-server devenv --datadir .state/tasks task verify-outlook` and verify an exact message read.
2. Recheck the consumer's selected-message references through the published MCP action.
3. Verify the draft and mutation guards against the actual supported response shapes.
4. Run the required Windows test, lint, format, Teams, Canvas, and schema checks.

Mailbox verification uses read-only operations. Sending or archiving needs separate explicit authorization.

The confirmation-dialog redesign and shorter read-failure path remain unfinished.
