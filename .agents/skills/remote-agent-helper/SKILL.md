---
name: remote-agent-helper
description: "Run assigned local environment or DB checks for a remote task, coordinated through its PR."
---

# Local PR helper

Declare this skill at session start. Resolve task card and unique PR from the
task ID; ask only if repository context cannot identify them. Read AGENTS, card
and PR body, then only required contracts. Verify checkout, candidate revision
and ownership. For assigned tests/DB work, follow test-runner and the relevant
COLLABORATION sections: serialize shared test-schema access, use no public test
records without explicit Superuser approval, and clean synthetic records.
Before DB bootstrap, verify fresh `origin/dev` Alembic head against the resolved
shared test-schema revision and candidate graph. Report unexplained drift as a
baseline/integration failure, not a branch defect; do not stamp or reset around
it. For a task-isolated schema, record its explicit dev baseline and task head.
Publish tested revision, exact commands, DB scenario counts, baseline and cleanup
as a PR comment; ask the
remote agent to include the linked evidence in the PR body. Do not self-accept,
change unowned files or infer merge/deploy permission.
