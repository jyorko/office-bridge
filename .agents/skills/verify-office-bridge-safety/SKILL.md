---
name: verify-office-bridge-safety
description: Run when changing durable Teams send, edit, Copilot, or Eyes work-tracking reservations; checks bounded crash and claim transitions against the real Python code.
---

# Verify Office Bridge write safety

An unsettled Teams write must keep its durable reservation so a retry cannot duplicate the action; Eyes may be removed only when Office Bridge established ownership and the last active claim ends, so a user's reaction is not removed.

## Run the check

For a user-installed copy, read `references/installation.md` to find the owning checkout. From that repository root, run:

```powershell
python scripts/verification/verify_safety.py
```

The runner invokes Office Bridge's real Python functions with temporary SQLite and JSON files. It substitutes only the external Teams/native calls. It never reads the user's account configuration, contacts Teams, or writes outside a temporary directory.

The runner checks the source hashes in `scripts/verification/source-hashes.json` before it imports production code. If a hash differs, inspect the changed transaction boundaries in the covered files, update the check if needed, rerun it and its scratch mutation check, then update the recorded hashes. Do not refresh hashes merely to make a stale check pass. Use `python scripts/verification/verify_safety.py --print-source-hashes` to print candidate hashes for review.

## What the check covers

`work_tracking.begin()` and `finish()` use a per-message SQLite coordinator. The verifier confirms that the fake Teams mutation sees `pending` already committed, and it explores all six legal begin/finish orders for two claims. It varies the initial Eyes observation (`selected`, `unselected`, or unavailable), each reachable add result (`reacted`, `already_reacted`, or uncertain), and each remove result after an owned add (`removed`, `already_removed`, or uncertain). A remove call must see zero active claims. A pre-existing Eyes reaction or an `already_reacted` add result must never lead to an automatic removal.

The crash cases raise a process-loss exception at the `messages.set_eyes()` boundary. The fake remote reaction either stays unchanged (loss before the click) or changes (loss after the click but before the caller receives a result). In both cases the durable marker remains `pending`; calls using the old or a new work ID cannot click again, and a new ID cannot create another claim. This checks the real SQLite commits around `_transition()` and the entry guards in `begin()` and `finish()`.

Three smaller checks exercise other write ledgers:

- `ledger.send_once()` is interrupted after its durable reservation and after an uncertain submit; repeating the request must not call submit again.
- `service.send_chat()` is interrupted after reservation/before native invocation, while the native call is uncertain, and after an observed native result but before the receipt write. Repeating any unsettled request must not invoke native again. A changed mention plan under a settled request ID must conflict.
- `service.edit_chat()` is interrupted after its `reserved` row is committed; repeating the request must not call the edit operation again.
- `copilot.ask()` is interrupted after its request file is reserved and its submit call begins; repeating the request must not call Copilot again.

These checks cover retry reservations only. The generic send cases stub the native status/send calls; they do not verify mention plan handling or native claim-digest binding. They also do not verify receipt validation, the `retryable_not_sent` send path, concurrent-process lock behavior, the native C#/JavaScript click guards, Teams identity resolution, or the full application.

## How to interpret the result

This is bounded state exploration against the Python implementation, not a proof of the whole repository. The bound is two work claims and every legal ordering of their begin/finish calls, with the listed observations and operation results. The script assumes SQLite commits and atomic file replacement behave as documented; injected process loss covers the external-call boundary, not power loss or storage corruption.

The first version was challenged with a scratch copy of `work_tracking.py` that omitted `pending` from the begin guard. The verifier failed on the trace “add transition becomes pending; process disappears; a new work ID calls begin and is added as a claim.” The scratch copy lived in a temporary directory and was deleted; production source was not edited for this check. If a future scratch variant fails somewhere unexpected, first compare the failing trace with the corresponding commit and call in `work_tracking.py`, `messages.py`, `ledger.py`, `service.py`, or `copilot.py`. Treat a mismatch as a modeling or harness error until the real code shows the same transition.

The current verifier also challenges a scratch copy of `service.py` with `_reserve_send()` moved after the native send call. It must reject that reservation-order mutation; the scratch copy is temporary and leaves production source unchanged.

## Coverage record

The baseline is repository commit `1198c39acae8b4aa8d47a67ed53bc49649c09a02`, plus the exact source versions recorded in `scripts/verification/source-hashes.json`. The hashes identify the checked implementation; the baseline commit alone does not.

| Source | Covered boundary |
| --- | --- |
| `src/office_bridge/teams/work_tracking.py` | `_transition`, `begin`, `finish`, and durable pending/claim state |
| `src/office_bridge/teams/messages.py` | `set_eyes` boundary only; it is replaced with injected outcomes, so its production wrapper is not exercised. Mention evidence sanitization is outside the durable-write proof |
| `src/office_bridge/teams/ledger.py` | `send_once` reservation, uncertain outcome, and retry guard |
| `src/office_bridge/teams/service.py` | Generic `send_chat` and `edit_chat` durable reservations, uncertain outcomes, and retry guards |
| `src/office_bridge/teams/copilot.py` | `ask` reservation and uncertain resubmission guard |

Move the sync point forward only after reviewing changed boundaries, rerunning the verifier, and repeating the scratch mutation check. After a source change that the verifier has not rechecked, use `/maintain-formal-verification-skill` to bring the coverage record and check back in sync.
