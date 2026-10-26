---
name: opencode-task
description: "Execute a bounded OpenCode task or coordinate its configured native tester."
---

# OpenCode executor

Declare this skill at session start. Resolve the task from its ID; read AGENTS,
ROADMAP row and card, then only linked contracts needed. Claim/status updates go
through the shared registry custodian. Work on the task-ID branch and assigned
paths; record actual docs read and checks in the card or PR body.

For tests, load test-runner. The configured `build` parent owns preflight and
cleanup, then invokes `tester` with OpenCode V2 `subagent` (not V1 `task` and
not as the parent agent). Keep the configured River model and subagent mode.
Tester runs only the supplied command and reports output; it does not edit source
or add tests. Other delegated work uses native children. Preserve evidence, then
stop/delete owned child sessions after dialogue; cleanup failure is housekeeping.
