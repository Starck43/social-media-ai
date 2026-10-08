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
