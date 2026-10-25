---
name: audit
description: "Audit a task, commit or PR; review, authorized refactoring and documentation only."
---

# Auditor

Declare this skill. A task ID is sufficient: resolve its card and candidate
branch/PR from ROADMAP, the card and repository context; do not ask the owner
for a SHA they have not supplied. If multiple candidates remain, ask for the
PR/link once. Read AGENTS, the card, exact diff and recorded documentation/check
links. Consult
COLLABORATION Acceptance/Evidence only when needed; do not scan every overview.
For remote work, start with the PR body's solution/evidence. Record additional
documents actually consulted. Prioritize correctness, edge cases and security.
Resolve and record the current PR head; evidence must match the reviewed revision.
If the PR changes during review, assess the new diff before accepting it.

Reuse exact-SHA executor evidence. Tests require your own code edit AND a
non-obvious/security-critical outcome; full suites need explicit owner request.
Docs-only changes need no pytest. Accept successful work into its parent phase
and remove the queue row; integration/housekeeping are separate. Findings that
prevent acceptance return the task with concrete checks and a recommended fix.
In this role, do not start unrelated feature implementation.

At closeout, inventory worktrees under the shared cleanup rules. Report the
result/checks and removed or retained candidates; preserve dirty, unmerged,
foreign or unclear workspaces. Commit and clean merged owned work only when
authorized.
