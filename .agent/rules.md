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

- **R6 — Protect the main checkout.** Do not switch, clean, stash or commit in
  `/Users/admin/Projects/social-media-ai` (the owner's `dev` checkout). Preserve
  the owner's existing local `.agent/rules.md` changes; do not overwrite them.
- **R7 — One PR, one review worktree.** Inspect `git worktree list` before
  creating anything. Reuse the existing PR worktree; do not create duplicates
  or reuse prunable/stale paths. Separate PRs keep separate worktrees.
- **R8 — Shared test schema; sequential execution only.** The owner's local
  review environment uses `test_schema` in `localhost:5432/social_manager`.
  Run pytest strictly sequentially across ALL review worktrees; only read-only
  review may run in parallel. Do not create/reset/drop/stamp/migrate schemas or
  run Alembic without an explicit command. This approval requirement overrides
  the automatic reset recommendations in R3/R4. Do not change the resolved
  private environment or connect to production. The remote agent only prepares
  tests/check commands; the owner/local agent executes them.
- **R9 — Explicit integration approval.** Merge into `dev` only after fresh
  compatibility review and an explicit owner command. Green tests are not merge,
  Ready, deploy or live-sender approval. No direct dev push or force-push.
