---
name: remote-task
description: "Execute one remote repository task through its PR and linked task card."
---

# Remote executor

Task ID and action are sufficient when repository context is available. Resolve
the repository from the assigned checkout/task card; create or locate the PR and
put its direct URL in the handoff. If the repository cannot be resolved, ask once
for its URL. Read AGENTS, PR body and card. Use only linked contract sections;
record actual reads/checks, source revision, solution and limits in the PR body.
Coordinate only through PR comments. One custodian synchronizes queue/card; do
not publish stale registry copies.
Apply the same task-ID branch, acceptance and safety rules as local work.
For environment-dependent checks consult [validation](references/local-validation.md).
If local DB access is needed, post the required scope/commands in a PR comment
and request local helper validation. Link its revision-specific result in the PR
body; report unrun checks honestly until it arrives.
