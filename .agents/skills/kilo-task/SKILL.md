---
name: kilo-task
description: "Execute one bounded Kilo task using only its native children."
---

# Kilo executor

Declare this skill at session start, then load `task` and follow it as the single
source of truth for claiming, branch/worktree, statuses, evidence and merge rules.
Apply only the deltas below on top of it.

- Delegate with Kilo native children only.
- Tests: load `test-runner`.
- Preserve evidence, then stop/delete owned child sessions after the dialogue.
  Unavailable session cleanup is housekeeping, not a task blocker.
