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
not `/tmp` or ignored `.agents/tmp`. Follow [COLLABORATION](../../../docs/process/COLLABORATION.md) only as needed.

## Delegation roles and order

The task executor remains responsible for the implementation, task registry and
final handoff. Subagents have separate roles and must not be treated as the
same model:

When the request says “solve `<ID>` with an auditor”, run this lifecycle: claim
the task and implement it; delegate the focused required test run to `tester`;
prepare the [audit handoff](../audit/SKILL.md#independent-reviewer-contract);
delegate the completed candidate to `auditor`; then either apply returned
corrections and repeat the test/audit cycle, or perform the documented
acceptance closeout. Do not report success before the audit verdict.

- **Implementation helpers**: use only configured general/explore subagents for
  bounded research or implementation work. They do not accept the task and do
  not audit it.
- **Tests**: delegate the exact test command to `tester`, pinned to
  `cent.ai/hy4-flash:free`. If `tester` is unavailable, the executor runs the
  command directly; do not silently substitute the auditor model. The tester is
  run-only and must return PASS/FAIL evidence for the exact revision.
- **Audit**: after implementation and required tests, send the concise handoff
  defined in [audit](../audit/SKILL.md) through this strict independent route:
  **Perch → free-pi → auditor**. Perch is the external review route; free-pi is
  `free-pi/deepseek-v4.1-flash#medium`; the final native reviewer is
  `cent.ai/gpt-6-luna#high`. `hy4` is not an audit fallback.

Fall through only when the previous audit route is unavailable or fails, and
record the route and model that actually ran. The auditor returns only
`ACCEPTED`, `CHANGES_REQUIRED`, or `BLOCKED` with concise evidence. The parent
keeps ownership and does not claim acceptance without the auditor's verdict;
after `ACCEPTED`, the executor performs the documented verified-integration
closeout and removes only its own merged task artifacts/worktree. Preserve
dirty, active, foreign, unmerged or unclear workspaces and report anything
retained. The auditor may update acceptance records according to the audit
skill, but does not perform destructive cleanup on behalf of the executor.
