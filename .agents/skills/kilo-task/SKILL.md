---
name: kilo-task
description: "Execute one bounded Kilo task using only its native children."
---

# Kilo executor

Declare this skill at session start. Resolve task card and repository from the
task ID; read AGENTS and only linked contracts needed. Use a task-ID branch and
assigned paths. Route claim/status updates through the shared registry custodian.
For testing, load test-runner. Use native children only. Record actual docs read,
source revision and checks in the card/PR body; report READY only when required
checks pass, otherwise task-related BLOCKED with exact results and next step.
Preserve evidence, then stop/delete owned child sessions after dialogue.
Unavailable session cleanup is housekeeping, not a task blocker.
