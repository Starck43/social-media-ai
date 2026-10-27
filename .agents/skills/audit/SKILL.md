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

On acceptance, update the parent phase with completion date, accepted SHA and a
brief result linked to `docs/audit/<id>.md`; remove the task card and ROADMAP row
for a closed task. An orphan leaves only its audit summary. Return concrete
findings, required checks and a recommended fix when not accepted: correctable
findings return the row to IN_PROGRESS, and remote findings ride PR comments,
never the PR body. A cannot-proceed task becomes BLOCKED with reason,
passed/failed/unrun checks and the recommended fix in the linked audit
diagnostic. An explicit owner defer becomes DEFERRED with the choice and resume
branch/PR in the row, never BLOCKED. An owner descope without acceptance is
recorded in the audit note or a Questions row and the row is removed. Session
or worktree cleanup never makes a resolved task BLOCKED.

At closeout, inventory worktrees and report removed/retained candidates under
[cleanup rules](../../../docs/COLLABORATION.md#git-and-cleanup). Preserve dirty,
active, unmerged, foreign or unclear workspaces. Commit and remove merged owned
work only when authorized; report actual checks briefly. For accepted code that
is not integrated, ask the owner whether to merge, continue on the branch/PR or
defer; the audit verdict itself is not merge authorization. Keep evidence in the
card/PR, never a local temporary path.
