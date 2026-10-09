# Development rules

- **R1 — Schema-qualified DDL.** Always use the explicitly resolved schema
  (`settings.DB_SCHEMA` or Alembic context); never rely on search_path or hardcode
  public/application schemas in migrations or maintenance commands.
- **R2 — Enum refactors.** Run `pytest --co` after changing an enum or its imports
  before running tests, to catch stale members and import failures early.
- **R3 — Full-suite-only failures.** First inspect test database state, fixtures,
  and cleanup ordering; reproduce on a freshly reset isolated database before
  changing application logic.
- **R4 — Interrupted runs.** Treat an interrupted test run as a dirty database.
  Use `python -m scripts.setup_test_db --reset` only against the resolved test
  database/schema, never production, before another run.
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
  column fix require an explicit owner instruction.
- **R9 — No merge without a command.** Merging any PR into `dev` happens only
  after compatibility is verified and the owner explicitly asks for it.
