---
name: task
description: "Execute one assigned project task from its task card and linked context."
---

# Task executor

Declare this skill at session start. A task ID and action are enough: resolve
the ROADMAP row, card, repository and task branch from context; ask only for
unresolvable details. Read AGENTS, ROADMAP and the card, then only linked
contracts needed for the change. Claim the task before editing and update its
status, owner and dialogue link in the shared registry through its custodian.

Work only on assigned paths in a task-ID branch/worktree. Record source revision,
documents actually read (path/section and purpose), checks and outcomes in the
card or PR body. Report READY only when required checks pass. Continue
IN_PROGRESS on actionable failures; use BLOCKED only when the solution/required acceptance cannot proceed,
with passed/failed/unrun checks and a recommended next step.
Never self-accept. Preserve data/session/branch cleanup as separate closeout
facts. Follow [COLLABORATION](../../../docs/COLLABORATION.md) only as needed.
