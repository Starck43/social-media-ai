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
- **R6 — Main folder is off limits.** `/Users/admin/Projects/social-media-ai`
  (branch `dev`) belongs to the owner and may hold dirty UI work. Never switch,
  clean, stash or commit there. Verification happens in worktrees only.
- **R7 — One worktree per PR.** Never verify two PRs in one working tree:
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
  any older automatic-reset advice. The remote agent prepares checks only;
  the owner/local agent executes them. Preserve the private environment.
- **R9 — No merge without a command.** Merging any PR into `dev` happens only
  after fresh compatibility is verified and the owner explicitly asks for it.
  A green test run is not Ready/deploy/live-sender approval. No direct dev push
  or force-push; never restore anonymous access or enable publication as a merge
  conflict workaround.
