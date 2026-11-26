---
name: audit
description: "Use when reviewing a READY task, commit or PR and issuing ACCEPTED / CHANGES_REQUIRED / BLOCKED; independent review, no implementation."
---

# Auditor

Declare this skill at session start. `/audit <ID>` or a task ID plus `audit` is
enough when the repository resolves the candidate. Start with ROADMAP's READY
row, task card/PR body and exact diff; read only linked contracts and check
evidence. Confirm the current candidate revision and that evidence matches it.
Do not implement features or UI: the audit returns corrections and a verdict,
and the only writes are documentation/registry updates. Full suites require an
owner request. This skill is the operating
checklist; [COLLABORATION](../../../docs/process/COLLABORATION.md) owns the
detailed contract and wins on any conflict.

## Verdict only

An audit produces one verdict, not a delivery. A prompt that adds "fix the
templates/styles/UI along the way" is a separate `task` lane: record it as a
follow-up, do not spawn implementers inside the audit. The 2026-11-26 P22-T08
session spent 5.8M input tokens (35%) on UI edits made during an audit that
still returned `CHANGES_REQUIRED`; the edits were premature and had to be
redone. If the diff is not ready to judge, return `CHANGES_REQUIRED` with the
missing work, not a branch you built yourself.

## Budget and preflight

- Start a clean session from the frozen candidate SHA. Do not continue a
  multi-day fork: it inherits a huge context and an inflated token counter.
- Read `git diff --stat` first, then only the changed hunks and their direct
  call sites. Never `cat` a whole subsystem or a whole template directory.
- Preflight the reviewer route once before spawning: `perch status`, `free-pi`
  availability/model ID, and the native `auditor` ID. A route that costs a child
  a turn to discover it is unavailable is a wasted child.
- Use at most one reviewer child per candidate. Send one blocking call with a
  timeout; no input-polling loops. Stop every child as soon as the verdict no
  longer depends on it.
- A child that exceeds ~1M input tokens or ~20 turns is stopped and narrowed;
  more context is not more certainty.
- DB evidence comes from the exact commands in the card on an isolated schema,
  not from exploring `setup_test_db.py`/`initdb`. Missing recipe is a
  `CHANGES_REQUIRED` finding, not an invitation to research.
- Keep high reasoning for the final verdict on the small diff; exploration,
  log parsing and inventories go to a cheap child.

## Independent reviewer contract

The audit must be performed by a model different from the task executor. Use the
strict route **Perch → free-pi → `auditor`**: Perch via
[perch-task](../perch-task/SKILL.md), then `free-pi/deepseek-v4.1-flash#medium`,
then the configured native auditor `cent.ai/gpt-6-luna#high`. The model ID is
`cent.ai/...`; an `opencode/cent.ai/...` prefix is not a registered model ID.
`hy4` is reserved for test execution and is not an audit fallback. Fall through
only when the previous route is unavailable or fails, and record the route/model
that actually ran. There is no route after `auditor`; report `BLOCKED` if the
final reviewer is unavailable.

Self-review is not acceptance. If one orchestrator implemented and is now
reviewing, it may run only the mechanical pre-flight; the verdict needs the
independent route. A same-session opinion never becomes `ACCEPTED`.

## Handoff

The executor sends this compact handoff; the reviewer does not implement:

```text
AUDIT HANDOFF
Task: <ID>
Candidate revision: <SHA>
Scope: <one sentence>
Acceptance criteria: <numbered list from the card>
Traceability: <criterion -> code path -> test -> evidence>
Changed paths: <paths>
Required checks: <commands, route, results>
Task-card/contract links: <links>
Question: accept this revision or return concrete corrections?
```

## Review

Independently inspect the logic, exact diff and test evidence. Green tests alone
are not acceptance. For every acceptance criterion confirm:

- the code path that implements it and whether it is reachable and fail-closed;
- the test that exercises that path, and that it fails on the pre-change revision
  (red-green) rather than passing either way;
- that required DB/integration scenarios ran on real PostgreSQL; unit or mock
  greens cannot replace them;
- that mocks cover only external, paid or irreversible boundaries and assert the
  interaction — a mock of the module under test, a weakened assertion or an
  expectation edited to match the implementation is a finding;
- that scope, tenant/actor rights, migrations and rollback match the card.

Return exactly one headline: `ACCEPTED`, `CHANGES_REQUIRED`, or `BLOCKED`,
followed by only the evidence, required corrections/checks and next action.
`ACCEPTED` authorizes the documented verified-integration closeout. On
`CHANGES_REQUIRED`, leave the task `IN_PROGRESS` and return concrete fixes for a
new revision and a repeated test/audit cycle. The executor's user-facing handoff
is one concise line with the verdict and next action; do not forward internal
reasoning or unrelated output.

## Acceptance and closeout

Before acceptance closeout, follow
[verified integration](../../../docs/process/COLLABORATION.md#acceptance):
require PR `mergedAt`/merge commit, fetch origin, prove ancestry and inspect
`git ls-tree origin/dev -- <delivered-file>` plus accepted content. Closed is not
merged; rejected/queued/unverified merges retain the row, card and resume branch.
Record review success separately; never publish DONE before integration proof.
Required DB scenarios need exact-revision local evidence and Alembic baseline
alignment. Only an explicit owner waiver of a named check is an exception under
Evidence; record it as unrun/waived, never PASS.

Only after verified integration, update the parent phase with completion date,
accepted SHA and a brief result linked to `docs/audit/<phase>/<id>.md`; move the
task card to `docs/archive/tasks/` and remove its ROADMAP row (see the audit
folder index for where a report lives). An orphan leaves only its audit summary.
Correctable findings return the row to IN_PROGRESS; remote findings ride PR
comments, never the PR body. A cannot-proceed task becomes BLOCKED with reason,
passed/failed/unrun checks and the recommended fix in the linked audit
diagnostic. An explicit owner defer becomes DEFERRED with the choice and resume
branch/PR in the row, never BLOCKED. An owner descope without acceptance is
recorded in the audit note or a Questions row and the row is removed. Session or
worktree cleanup never makes a resolved task BLOCKED.

At closeout, inventory worktrees and report removed/retained candidates under
[cleanup rules](../../../docs/process/COLLABORATION.md#git-and-cleanup). Preserve dirty,
active, unmerged, foreign or unclear workspaces. An accepted code revision is also
authorization to merge it and delete its branch, provided the accepted SHA is the
PR head, the PR targets `dev` and no finding blocks it; record the merge commit in
the audit note. Any other merge, continuation or deferral still asks the owner.
Keep evidence in the card/PR, never a local temporary path.
