---
name: audit
description: "Audit a task, commit or PR; authorized review, refactoring and documentation only."
---

# Auditor

Declare this skill at session start. `/audit <ID>` or a task ID plus `audit` is
enough when the repository resolves the candidate. Start with ROADMAP's READY
row, task card/PR body and exact diff; read only linked contracts/check evidence.
Confirm the current candidate revision and that evidence matches it. If review
finds corrections, decide whether focused checks are needed; do not run tests
solely as routine audit. Full suites require owner request. Do not take feature
implementation beyond authorized corrections/refactoring/docs.

## Independent reviewer contract

The audit must be performed by a model different from the task executor. Use
the strict route **Perch → free-pi → `auditor`**: Perch via
[perch-task](../perch-task/SKILL.md), then
`free-pi/deepseek-v4.1-flash#medium`, then the configured native auditor
`cent.ai/gpt-6-luna#high`. The model ID is `cent.ai/...`; an
`opencode/cent.ai/...` prefix is not a registered model ID. `hy4` is reserved
for test execution and is not an audit fallback. Fall through only when the
previous route is unavailable or fails, and record the route/model that
actually ran. There is no route after `auditor`; report `BLOCKED` if the final
reviewer is unavailable.

The executor must send this compact handoff, without asking the reviewer to
implement the task:

```text
AUDIT HANDOFF
Task: <ID>
Candidate revision: <SHA>
Scope: <one sentence>
Changed paths: <paths>
Required checks: <commands and results>
Task-card/contract links: <links>
Question: accept this revision or return concrete corrections?
```

The reviewer is read-only for source code and returns exactly one headline:
`ACCEPTED`, `CHANGES_REQUIRED`, or `BLOCKED`, followed by only the evidence,
required corrections/checks and next action. It must independently inspect the
logic, exact diff and test evidence; green tests alone are not acceptance.
`ACCEPTED` authorizes the documented verified-integration closeout. On
`CHANGES_REQUIRED`, leave the task `IN_PROGRESS` and return the concrete fixes
to the executor for a new revision and repeat the test/audit cycle. The
executor's user-facing handoff is one concise line with the verdict and next
action; do not forward the reviewer's internal reasoning or unrelated output.

Before acceptance closeout, follow [verified integration](../../../docs/process/COLLABORATION.md#acceptance):
require PR `mergedAt`/merge commit, fetch origin, prove ancestry and inspect
`git ls-tree origin/dev -- <delivered-file>` plus accepted content. Closed is not
merged; rejected/queued/unverified merges retain the row, card and resume branch.
Record review success separately; never publish DONE before integration proof.
Required DB scenarios need exact-revision local evidence and Alembic baseline
alignment; unit/mock greens or deferred checks cannot substitute for them. Only
an explicit owner waiver of a named check is an exception under Evidence; record
it as unrun/waived, never PASS.

Only after verified integration, update the parent phase with completion date,
accepted SHA and a brief result linked to `docs/audit/<phase>/<id>.md`; move the
task card to `docs/archive/tasks/` and remove its ROADMAP row for a closed task
(see the audit folder index for where a report lives). An orphan leaves only its
audit summary. Return concrete
findings, required checks and a recommended fix when not accepted: correctable
findings return the row to IN_PROGRESS, and remote findings ride PR comments,
never the PR body. A cannot-proceed task becomes BLOCKED with reason,
passed/failed/unrun checks and the recommended fix in the linked audit
diagnostic. An explicit owner defer becomes DEFERRED with the choice and resume
branch/PR in the row, never BLOCKED. An owner descope without acceptance is
recorded in the audit note or a Questions row and the row is removed. Session
or worktree cleanup never makes a resolved task BLOCKED.

At closeout, inventory worktrees and report removed/retained candidates under
[cleanup rules](../../../docs/process/COLLABORATION.md#git-and-cleanup). Preserve dirty,
active, unmerged, foreign or unclear workspaces. Commit and remove merged owned
work when authorized; report actual checks briefly. An accepted code revision is
also authorization to merge it and delete its branch, provided the accepted SHA is
the PR head, the PR targets `dev` and no finding blocks it; record the merge
commit in the audit note. Any other merge, continuation or deferral still asks the
owner. Keep evidence in the card/PR, never a local temporary path.
