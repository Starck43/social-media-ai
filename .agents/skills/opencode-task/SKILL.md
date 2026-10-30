---
name: opencode-task
description: "Execute a bounded OpenCode task or coordinate its configured native tester."
---

# OpenCode executor

Declare this skill at session start, then load `task` and follow it as the single
source of truth for claiming, branch/worktree, statuses, evidence and merge rules.
Apply only the deltas below on top of it.

- Delegate with native children. Use OpenCode V2 `subagent`, never V1 `task`, and
  never start the configured tester as the parent agent.
- Tests: load `test-runner`. The configured `build` parent owns preflight and
  cleanup, then invokes `tester` in subagent mode and keeps its configured River
  model. Tester runs only the supplied command and reports output; it does not
  edit source or add tests.
- Preserve evidence, then stop/delete owned child sessions after the dialogue.
  Cleanup failure is housekeeping, not a task blocker.
