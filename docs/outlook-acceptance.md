# Outlook web acceptance

The Windows host compiles and verifies the configured account in its hidden WebView2 session. Inbox listing and literal search work against the live mailbox.

The consumer's two conversation references pass repeated selected-message reads on Windows. Outlook fetches mail in background workers; the host now observes those network sessions as well as document responses. A read binds the single non-draft item in an opened-message response to the displayed body using a SHA-256 digest. List metadata alone cannot establish that binding.

The native draft, reply, send, archive, and attachment handlers are implemented and their public routes are enabled. Canvas reply controls follow the complete message version and the observed composer capability. Send and archive still require exact identities, unchanged versions, and separate authorization.

The reader excludes icon-font glyphs from inbox subject extraction. Response parsing handles WCF type markers and supported REST field names. Host restart checks the loaded source hash to prevent testing an older native build.

Before merging:

1. Run `action-server devenv --datadir .state/tasks task verify-outlook` and verify an exact message read.
2. Recheck the consumer's selected-message references through the published MCP action.
3. Verify the draft and mutation guards against the actual supported response shapes.
4. Run the required Windows test, lint, format, Teams, Canvas, and schema checks.

Mailbox verification uses read-only operations. Sending or archiving needs separate explicit authorization.

Individual reads no longer scan the entire inbox before locating their reference. The pipe timeout is 35 seconds; the selected-pane wait is bounded to eight seconds.
