---
name: develop-social-media-ai
description: Develop social-media-ai remotely through GitHub PRs and hand off environment-dependent verification to a local agent.
---

# Remote Social Media AI
Read current AGENTS.md, .agent/rules.md and .agent/workflow.md. Use docs/PRODUCT_PLAN.md for product direction, docs/BUSINESS_PRODUCTION_READINESS.md for engineering gates, docs/design/README.md for tasks/ownership and docs/IMPLEMENTATION_STATUS.md for delivered foundations.

- Confirm repository access and execution capabilities. Read exact file versions and fresh dev/PR heads; preserve concurrent work. Create one bounded thematic branch from dev and PR to dev.
- Read only the selected readiness gate, subsystem contract and relevant call sites. Follow current architecture and ORM/service conventions; avoid unrelated refactoring, speculative dependencies and frozen runtime paths.
- Add small meaningful regression checks. Execute only isolated checks permitted by current .agent/rules.md and available environment. Never run owner-local DB tests, schema setup/reset/migrations, real providers or live actions remotely.
- If project .venv or dependencies are unavailable, report the blocker instead of using system Python. Do not claim prepared checks have passed.
- Hand full regression, database, integration and environment-dependent validation to the local agent with branch, exact implementation SHA, commands and acceptance criteria. Use references/local-validation.md when a detailed validation assignment is needed.
- Keep PR draft while required evidence is outstanding. Recheck heads before accepting reports, preserving their original SHA and attribution. Follow current owner authorization for Ready/merge; do not infer deployment or schema permission.
- Before session switches, update the PR's compact current handoff using .agent/workflow.md. Use the existing board for ownership/status and ledger for delivered evidence; do not create a second task list.
- Coordinate through PR descriptions and owner-relayed reports. Do not assume local filesystem access or automatic conversation sync. Person-directed comments require explicit authorization.
