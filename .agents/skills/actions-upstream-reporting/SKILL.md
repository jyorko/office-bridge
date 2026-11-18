---
name: actions-upstream-reporting
description: Triage failures encountered while using the community Actions Runtime or Actions Core and report confirmed defects to joshyorko/actions.
---

# Actions upstream reporting

Use this skill when an Office Bridge task encounters a possible defect in
`actions-runtime` or `actions-core`. Follow the evidence-first approach used
by the upstream-contribution skill in `joshyorko/josh-all-the-things`.

## Establish the cause

Read the consuming repo's instructions and RCC Action Server skill. Record the
installed package versions, relevant configuration, exact failing command or
MCP request, expected behavior and observed behavior. Inspect the downloaded
runtime implementation and the matching `joshyorko/actions` source revision.
Build the smallest synthetic reproduction that isolates the framework from
Office Bridge's code, package configuration and corporate services.

Classify the finding before publishing. A local package or integration error
belongs in Office Bridge. A confirmed runtime or core defect belongs in
`joshyorko/actions`. A missing capability is a feature request. An uncertain
attribution stays under investigation. Investigate supplied failures directly.
Use ClawPatch discovery only when the user asks for a discovery run.

## Report confirmed defects

Read the target's current contribution guidance, issue forms and security policy.
Search its open and closed issues and pull requests for the same root cause.
Link a matching report, or add material reproduction evidence to it.

Office Bridge's `AGENTS.md` authorizes reporting reproducible Action Server
bugs to `joshyorko/actions`. Use that authorization without asking again.
For another target, use the active task's authorization.

File one focused issue for a new confirmed defect. Include the affected versions,
platform, expected and actual results, synthetic reproduction, sanitized output,
source evidence and downstream impact. Keep credentials, account identities,
mail/chat content, profiles and private paths out of public reports. Follow the
target's private disclosure route for security-sensitive findings.

Reporting a defect does not authorize unrelated upstream code, examples,
templates or pull requests. A requested local integration remains the delivery
goal. Continue authorized Office Bridge fixes and verification while tracking
the upstream report.

## Follow through

Reuse the report and reproduction when new evidence arrives. Follow up for a
maintainer question, a correction or a relevant fix/release. Verify the version
Office Bridge actually consumes before describing the problem as resolved.
Keep any temporary local mitigation narrow and tested, with a removal condition
tied to the verified upstream fix.

Report the classification, reproduction, issue link or local correction, affected
consumer and remaining uncertainty. If investigation found no confirmed
upstream defect, say so. Preserve the distinction between a filed report,
merged fix, released package and a consumed-and-verified fix.
