# Teams work tracking

Work tracking is an opt-in lifecycle for Eyes on explicitly authorized task messages. It does not authorize a reply, resolution, or other work on the task. Do not add a default completion reaction or reply.

## Opt in to exact chats

Call consequential action `teams_work_tracking_configure(session_id, chats, enabled)` only after the user opts in. `chats` is a list of exact `{chat_id, title}` references, with at most 50 chats. Enabling requires at least one chat. A session's chat scope is immutable; use a new `session_id` to change it. Disabling blocks new claims but still permits authorized cleanup of existing claims.

Treat `session_id` as the agent's opt-in scope and `work_id` as the stable identity of one claim. Both accept 1–128 letters, digits, period, underscore, or hyphen.

## Claim and finish work

Call `teams_work_begin(session_id, work_id, chat, message_id, expected_content)` when beginning meaningful investigation or execution of an assigned task, not while passively reading or waiting in a queue. Use the exact chat and message ID from current Teams results and `content_text` from `teams_web_message_reactions` as `expected_content`. The task message must still match.

The first active claim adds Eyes only if it is available and not already selected. Pre-existing Eyes is preserved. Additional active claims share the marker; when the last active claim ends, Eyes can be removed only if Office Bridge recorded that it added it. Claims and marker ownership persist across disposable worker replacement.

Call `teams_work_finish(session_id, work_id, chat, message_id, outcome)` when that claim ends. `outcome` is `completed`, `paused`, `cancelled`, or `failed`; use `completed` only when the requested outcome was verified. An interim reply does not finish still-active investigation.

## Reconcile

`teams_work_status(chat, message_id)` reads durable claims. For owned or pre-existing markers it also observes current task content and Eyes state without clicking Teams controls; pending and uncertain markers return ledger state only. Review `marker`, `active_claims`, `claims`, and `warning` before resuming or cleanup. A stale claim does not mean success and does not expire automatically.

Pending, uncertain, or externally changed reaction states block automatic add/remove. Do not create a new work ID to get around that block. An uncertain transition must be resolved from the current message and durable status before any further action. If status reports an abandoned claim, explicitly finish that exact claim with its known outcome; never infer `completed` from age or disappearance of a worker.

Replies and resolution are independent from claims. Finishing a claim changes the Eyes marker only when ownership rules allow it; it sends no message and adds no completion emoji.
