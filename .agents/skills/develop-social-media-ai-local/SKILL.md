---
name: develop-social-media-ai-local
description: Implement or verify social-media-ai in a local IDE or CLI checkout using the project virtualenv and isolation only when needed.
---

# Local Social Media AI
Read AGENTS.md, .agent/rules.md and .agent/workflow.md from the current checkout. Use docs/PRODUCT_PLAN.md for product direction, docs/BUSINESS_PRODUCTION_READINESS.md for engineering gates, docs/design/README.md for tasks/ownership and docs/IMPLEMENTATION_STATUS.md for delivered foundations.

- Inspect git status and git worktree list first. Use the current `dev` checkout for one sequential local task when its diff is coherent; preserve unrelated work and never switch, stash or clean owner changes. Use a separate worktree only for parallel work, PR review, risky experiments, conflicts or an explicit owner request.
- Implement the selected bounded task using current managers/services/session lifecycle. Follow repository conventions rather than duplicating them here.
- Use .venv/bin/python and .venv tools from the worktree root. If missing, report the setup need; do not install globally or copy secrets into Git.
- Before pytest, inspect conftest/setup helpers and resolve the isolated test target without exposing secrets. Shared test schemas require sequential runs; verify no other test owner is active. Do not create/reset/drop/stamp/migrate schemas without the owner's explicit instruction.
- Start with .venv/bin/python -m pytest -o addopts='' <relevant-file-or-test> -q. Add meaningful regression tests for behavior changes. Broaden checks for shared contracts and run the full suite for broad changes when safe and available. Do not rerun already accepted checks without a changed risk.
- For PR verification, pin the requested SHA and distinguish existing baseline failures from regressions. Return exact commands, revision and results; stop on uncertain environment state.
- After validation, propose a coherent commit split/messages and offer to create the commits directly in `dev`. A closeout phrase defined in `AGENTS.md` authorizes committing completed, validated, task-scoped changes without another question; exclude and report unrelated, incomplete or unvalidated changes. Use a task branch/PR only when isolation or publication is needed; never push `dev` without explicit authorization.
- Update the board/ledger and any existing PR handoff before switching sessions. Follow .agent/workflow.md for task status/evidence and resume.
