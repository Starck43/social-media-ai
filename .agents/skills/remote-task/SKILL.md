---
name: remote-task
description: "Execute one remote repository task through its PR and linked task card."
---

# Remote executor

Declare this skill at session start. A task ID and action are enough when the
checkout resolves the repository. Read AGENTS, task card and PR body; load only
linked contracts needed. Use a task-ID branch. Put the direct repository/PR link,
solution, source revision, actual documents read, checks and limitations in the
PR body. Send all coordination, questions and status changes as PR comments.
The designated custodian synchronizes ROADMAP/task records; do not publish stale
registry copies. Claim = the scoped claim commit you publish to origin/dev yourself before
edits, never deferred to the solution merge. Do not stop or wait for a custodian
or the owner between steps; ask a question only when you decide one is needed. For local DB/environment checks, request them in a PR comment
and link the revision-specific result in the PR body. Report unrun checks.
Report READY in a PR comment only when every required acceptance check passed;
the custodian applies the row change. Do not merge or close a PR without the
owner's explicit choice; use PR comments for the choice and handoff. Preserve
the branch/PR if work will continue or be deferred.
