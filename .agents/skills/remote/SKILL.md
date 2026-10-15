---
name: remote
description: Develop social-media-ai remotely through GitHub PRs and hand off environment-dependent verification to a local agent.
---

# Remote Social Media AI
Read current AGENTS.md, .agent/rules.md and .agent/workflow.md. Use docs/PRODUCT_PLAN.md for product direction, docs/BUSINESS_PRODUCTION_READINESS.md for engineering gates, docs/design/README.md for tasks/ownership and docs/IMPLEMENTATION_STATUS.md for delivered foundations.

- Confirm repository access and execution capabilities. Read exact file versions and fresh dev/PR heads; preserve concurrent work. Create one bounded thematic branch from dev and PR to dev.
- Read only the selected readiness gate, subsystem contract and relevant call sites. Follow current architecture and ORM/service conventions; avoid unrelated refactoring, speculative dependencies and frozen runtime paths.
- Add small meaningful regression checks. Apply the remote no-database exception below; retain .agent/rules.md R8/R10 for real/shared-database and environment-dependent validation. Never run owner-local DB tests, schema setup/reset/migrations, real providers or live actions remotely.
- For remote isolated checks, create a separate sandbox virtual environment with the selected test dependencies matching repository manifests; do not substitute system Python for the owner's project .venv. If dependencies cannot be installed, report the specific blocker. Do not claim prepared checks have passed.
- Hand full regression, database, integration and environment-dependent validation to the local agent with branch, exact implementation SHA, commands and acceptance criteria. Use references/local-validation.md when a detailed validation assignment is needed.
- Keep PR draft while required evidence is outstanding. Recheck heads before accepting reports, preserving their original SHA and attribution. Follow current owner authorization for Ready/merge; do not infer deployment or schema permission.
- Before session switches, update the PR's compact current handoff using .agent/workflow.md. Use the existing board for ownership/status and ledger for delivered evidence; do not create a second task list.
- Coordinate through PR descriptions and owner-relayed reports. Do not assume local filesystem access or automatic conversation sync. Person-directed comments require explicit authorization.

## Remote no-database testing exception (owner-authorized)

- Run genuinely database-independent tests directly in the remote agent's own isolated environment, including pytest unit tests, mocked service/handler tests and offline browser fixtures. For this narrow remote scope, Kilo and the owner's project .venv are not prerequisites; this is an explicit exception to the blanket pytest-routing language in AGENTS.md and .agent/rules.md R10, not a change to local/shared-DB execution.
- If the existing suite is incomplete or a real environment is unavailable, add/run a focused synthetic or mocked subset for the implemented behavior. Incompleteness alone never makes a DB-dependent test safe: replace DB/provider boundaries with explicit mocks before execution, or leave that case deferred. Never present mocked coverage as real DB, ASGI integration, permission/tenant-isolation or full acceptance evidence.
- Inspect the selected tests and their import/fixture graph first. Use explicit test paths, disable root conftest and cache for isolated pytest (`--noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header`). Use no real secrets, .env files, database URLs, credentials or external sends; use clearly fictitious nonsecret configuration only if an isolated import requires it. Browser fixtures must intercept/abort external requests and mock application endpoints. Do not start the application/runtime or contact any real DB/provider to make a synthetic check pass.
- Pin source HEAD; record environment/dependency versions, exact commands, exit codes and passed/failed/skipped counts. Keep unrelated changes intact. Distinguish sandbox-executed evidence from owner-reported and deferred evidence, and enumerate the missing real-environment scenarios. A passing subset is not a passing full suite; do not weaken existing assertions merely to obtain green output.
- Keep real/shared-DB, full integration and owner-environment checks on the existing sequential Kilo test-orchestrator -> test-runner -> pare-test route under R8/R10. No schema creation/reset/drop/stamp/migration, deployment or live action is authorized by this exception. Keep a feature PR Draft while its required real-environment acceptance remains outstanding.
