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

Work only on assigned paths in a task-ID branch/worktree. The primary checkout
is the owner's: never switch, move or commit branches in it; open a worktree
instead. Record source revision,
documents actually read (path/section and purpose), checks and outcomes in the
card or PR body. Report READY only when required checks pass. Continue
IN_PROGRESS on actionable failures; use BLOCKED only when the solution/required acceptance cannot proceed,
with passed/failed/unrun checks and a recommended next step.
Never self-accept or merge/push a solution without the owner’s explicit
choice. Report merge / continue / defer options with the exact resume branch/PR;
keep the work intact while awaiting a decision. Store handoff in the card/PR,
not `/tmp` or ignored `.agents/tmp`. Follow [COLLABORATION](../../../docs/COLLABORATION.md) only as needed.

## Delegation order

Delegate one bounded, self-contained unit of work down this order and stop at the
first route that is available:

1. **Perch** — external CLI, headless and review-gated
   (`perch run "<task>" --json --cwd <dir> --permission auto_review`); only Codex
   may invoke third-party agents, every other caller starts at route 2. Route
   details: [perch-task](../perch-task/SKILL.md).
2. **free-pi** — native subagent on the free-pi daily free allowance: model
   `free-pi/deepseek-v4.1-flash#medium` (DeepSeek 4.1 Flash, thinking medium).
   The provider and its token file live in the global OpenCode config. The
   `free-pi-cli` itself is a TUI-only agent with no `-p/--model/--thinking`, so
   address free-pi through the provider, never as that CLI process.
3. **river** — native subagent on
   `river/deepseek-ai/DeepSeek-V4.1-Flash-1017`.

Fall to the next route only when the previous one is unavailable or fails, and
report which route actually ran. Parent keeps judgment, acceptance and the
registry; a delegated route returns only its verdict and evidence.
