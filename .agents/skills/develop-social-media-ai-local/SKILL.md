---
name: develop-social-media-ai-local
description: Implement or verify social-media-ai in a local IDE or CLI checkout using worktrees and the project virtualenv.
---

# Local Social Media AI
Read AGENTS.md, .agent/rules.md and .agent/workflow.md from the current checkout. Use the audit for direction, docs/design/README.md for tasks/ownership and docs/IMPLEMENTATION_STATUS.md for completion evidence.

- Inspect git status and git worktree list first. Preserve the owner's main checkout; never switch, stash, clean or commit in the protected path named in .agent/rules.md. Reuse/create one worktree per task/PR from fresh dev.
- Implement the selected bounded task using current managers/services/session lifecycle. Follow repository conventions rather than duplicating them here.
- Use .venv/bin/python and .venv tools from the worktree root. If missing, report the setup need; do not install globally or copy secrets into Git.
- Before pytest, inspect conftest/setup helpers and resolve the isolated test target without exposing secrets. Shared test schemas require sequential runs; verify no other test owner is active. Do not create/reset/drop/stamp/migrate schemas without the owner's explicit instruction.
- Start with .venv/bin/python -m pytest -o addopts='' <relevant-file-or-test> -q. Add meaningful regression tests for behavior changes. Broaden checks for shared contracts and run the full suite for broad changes when safe and available. Do not rerun already accepted checks without a changed risk.
- For PR verification, pin the requested SHA and distinguish existing baseline failures from regressions. Return exact commands, revision and results; stop on uncertain environment state.
- Commit on the task branch and create/update a PR to dev. Apply actual owner merge authorization and repository safeguards; do not assume a green small test permits merge.
- Update the PR's current handoff before switching sessions. Follow .agent/workflow.md for task status/evidence and resume. Remote-agent reports are attributed evidence, not locally executed tests.
