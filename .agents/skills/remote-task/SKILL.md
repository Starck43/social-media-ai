---
name: remote-task
description: "Execute one remote repository task through its PR and linked task card."
---

# Remote executor

Require repo URL, task ID and PR URL. Read AGENTS, PR body and selected card.
Use only task-linked contract sections; record actual reads/checks in the PR body
with source SHA, solution and limitations. All coordination uses PR comments.
One custodian synchronizes queue/card; do not publish stale registry copies.
Apply the same task-ID branch, acceptance and safety rules as local work.
For environment-dependent checks consult [validation](references/local-validation.md).
