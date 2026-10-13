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
  `test_schema` on `localhost:5432/social_manager`, so at most one pytest may run
  at a time; parallel PR checks are code-review-only until separate schemas are
  prepared. Never create, reset, drop, stamp or migrate schemas automatically —
  `setup_test_db --reset`, `DROP`, `alembic` and the owner's manual 0088-style
  column fix require an explicit owner instruction. These restrictions override
  any older automatic-reset advice. Delegation through Kilo's `test-orchestrator`
  to its local `test-runner` child is authorized; it must execute requested tests
  sequentially with these restrictions.
  Preserve the private environment.

- **R10 — Kilo owns test execution.** Start Kilo's `test-orchestrator` with
  `kilo run --dir <worktree> --agent test-orchestrator --format json --file <task-file>`;
  it delegates to the project's `test-runner` child using Kilo's `task` tool.
  The child must use the connected `pare-test` MCP `run` tool, passing the project
  `.venv/bin/python` and `-o addopts='' -q --tb=short --no-header`. Never run
  pytest in the coordinating Codex process or fall back to shell pytest in Kilo.
  Kilo's global config is `~/.config/kilo/kilo.jsonc`; the active server is
  `mcp.pare-test`. Runs remain sequential under R8.
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
