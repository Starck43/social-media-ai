# Development rules

- **R1 — Schema-qualified DDL.** Always use the explicitly resolved schema
  (`settings.DB_SCHEMA` or Alembic context); never rely on search_path or hardcode
  public/application schemas in migrations or maintenance commands.
- **R2 — Enum refactors.** Run `pytest --co` after changing an enum or its imports
  before running tests, to catch stale members and import failures early.
- **R3 — Full-suite-only failures.** First inspect logs, test database state,
  fixtures and cleanup ordering without modifying data. Reproduce only after
  verifying the shared review environment is safe and no other pytest is active.
  A reset/new schema is NOT automatic: obtain explicit approval under R8 before
  any schema operation. Do not change application logic from an unverified cause.
- **R4 — Interrupted runs.** An interrupted run may leave dirty data or a live
  child process; it is incomplete evidence, never a green result. Check both
  read-only before another run. If state is uncertain, stop and ask; do not
  automatically reset/drop/create/stamp/migrate. Any separately approved reset
  must target the explicitly resolved TEST schema/database, never production.
- **R5 — Documentation sync.** Finish each phase by syncing docs to the actual
  code, migrations, permissions and CLI/API contracts. Record test results and
  limitations; do not claim a green suite without a completed run.
- **R6 — Preserve local owner work.** `/Users/admin/Projects/social-media-ai`
  (branch `dev`) is the primary sequential development and integration checkout.
  Inspect status before changes; never discard, stash, or commit unrelated work.
  For ordinary work, direct local commits in `dev` require owner acceptance of
  the proposed coherent commit split/messages. A user-started ROADMAP Goal that
  explicitly requires a commit after each accepted task authorizes those local,
  task-scoped commits without another prompt. Closeout phrases in `AGENTS.md`
  authorize validated task-scoped commits as well.
  Use an isolated worktree only when parallel work, PR review, risky
  experimentation or conflicting changes require it.
- **R7 — Isolate PR review and parallel work.** Never verify two PRs in one working tree:
  PR #22 → `social-media-ai-pr22-review`, PR #30 (`refactor/tenancy-model-layout`)
  → its own worktree, PR #31 (`refactor/notification-model-layout`) → its own
  worktree. Check `git worktree list` before creating anything; reuse existing
  folders. `/private/tmp/smai-head` is stale/prunable — do not use it.
- **R8 — Shared test schema, sequential only.** Every worktree resolves the same
  `test_schema` on `localhost:5432/social_manager`, so checks that use it must
  remain sequential; do not overlap DB-backed tests or separate execution routes
  against that target. Independently proven no-DB scopes may run in parallel.
  Parallel PR checks that use the shared schema remain code-review-only until
  separate schemas are prepared. Never create, reset, drop, stamp or migrate
  schemas automatically —
  `setup_test_db --reset`, `DROP`, `alembic` and the owner's manual 0088-style
  column fix require an explicit owner instruction. These restrictions override
  any older automatic-reset advice. Test delegation follows R10 and these
  restrictions.
  The resolved target itself is pre-approved for real ASGI/DB test execution
  (including `httpx` `ASGITransport` tests that create and clean up their own
  data); never ask the owner to reconfirm the target for such runs. This
  pre-approval covers running tests only — never reset, drop, migrate or reseed
  — and an uncertain or interrupted state still stops under R4.
  Preserve the private environment.

- **R10 — OpenCode owns primary test execution.** Delegate every assigned pytest
  scope through `.codex/agents/test-runner.toml`, using OpenCode `build` pinned to
  `QwenPlan/deepseek-v4-flash-0731` with that exact model name (no `opencode/`
  prefix). The primary invocation is
  `opencode run --dir <worktree> -m QwenPlan/deepseek-v4-flash-0731 --auto "<message>" --file <task-file>`.
  Verify the task file is attached and readable. Collect before execution after
  enum/import changes. Use `.venv/bin/python -m pytest`, assigned paths as
  positional arguments, and isolated flags `--noconftest -o addopts= -q --tb=short --no-header`.
  Independent scopes may run in parallel only after proving they do not touch the
  shared DB/schema or other shared mutable state; shared PostgreSQL checks remain
  sequential under R8. Neither the coordinating Codex process nor Codex subagents
  execute pytest directly. OpenCode executes assigned pytest commands through
  its tools; there is no direct Codex shell fallback. Never reset/drop/stamp/
  migrate/seed, call live providers, or run external writes as part of test
  delegation. Record exact collection and execution results in
  `.agent/test-report.md`.
  Kilo's configured `test-orchestrator` → `test-runner` route is secondary and may
  be used only when the primary OpenCode route is BLOCKED; preserve the same scope,
  DB, evidence, and no-destructive-action safeguards. Do not use the obsolete
  OpenCode step5 fallback configuration.
- **R9 — Delegated routine Ready/merge, safeguards retained.** The owner gives
  standing authorization to perform routine Ready/merge of agreed bounded
  packages after verifying fresh PR/dev heads, compatibility, required checks
  and relevant review evidence. Keep Draft while necessary evidence is pending;
  do not ask for confirmation at each routine step. Stop and clarify conflicts,
  unexpected PR-head changes, failing checks, unclear dependencies/ownership or
  elevated risk. Do not rerun completed checks without a specific changed risk.
  This is NOT schema/reset/migration/live/deploy or required-check-change
  permission. No direct dev push or force-push; never restore anonymous access
  or enable publication as a merge conflict workaround.
