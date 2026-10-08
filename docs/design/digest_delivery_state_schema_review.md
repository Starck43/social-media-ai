# Digest delivery state schema unit

## Scope and approval

The owner explicitly selected one dedicated DigestRun JSONB field plus a
separate migration for durable retry checkpoints. This branch implements only
that storage foundation. It does **not** implement receipts, part-level resume,
publication locks, force-generation policy or ambiguous acknowledgement handling.
No production migration/deployment or live messenger call is authorized here.

Started from dev `7fedbd3b769ab5c9239be6f3cf852ac9fd2745a9`. The local
integration snapshot now preserves UI dev `7a0637412ae761fff83f26fb4f8b5a581dcf6e28`;
its full check is in progress. The migration/model do not overlap those UI edits.
The new universal-assistant product documents are preserved. This digest field
is not the future general-purpose outbox/workflow schema.

## Implemented field and migration

- `DigestRun.delivery_state`: nullable PostgreSQL JSONB, no default/server default.
- Revision `0087`, parent `0086`; one new column on the existing digest_runs table,
  no new table/index and no changes to recipients, content, status or llm_cost.
- Both upgrade/drop explicitly use the schema resolved from settings.DB_SCHEMA.
- Existing rows get NULL; migration does not fabricate per-target successes or
  assume that a legacy partial run is completely unsent.
- Existing content/message/status/cost fields and old sender code remain compatible.
- Runtime checkpoint writers/readers must be introduced in the next implementation
  PR with a versioned, validated payload and tenant/target guards.

## Rollout and rollback boundary

Apply the schema before deploying code that selects/writes this field. Until
that runtime lands, the application still does the old aggregate retry behavior;
adding the column alone does not suppress duplicates.

Before downgrade, pause delivery workers and retain any checkpoint evidence.
Dropping the column destroys receipts. Never downgrade an active sender and then
blindly resume previously partial deliveries. The unit test confirms old report
rows survive a rollback, not that checkpoint evidence survives dropping its column.

No production DB was inspected or altered, and no deployed revision is asserted.

## Verification

Local checks use sandbox PostgreSQL 15.18, database digest_tests/schema digest_test,
Python 3.13, the project venv and mocked transports. No production data/secrets.

- Actual local test-schema upgrade from 0086 to 0087 completed.
- Isolated-schema upgrade, JSON write, downgrade and re-upgrade preserve old
  report rows and leave old checkpoint history NULL.
- PostgreSQL/SQLite/base-manager + migration + digest regressions: **62 passed,
  1 warning** (3.74 s).
- Full suite on dev 7fedbd3 plus the schema: **1077 passed, 1 skipped,
  10 warnings** (259.79 s), coverage disabled, fresh isolated test schema.
- Alembic check: **No new upgrade operations detected**.
- An earlier full run found 19 SQLite compilation errors from a direct ORM JSONB
  declaration. Reproduced after fresh test reset; fixed the model with a JSON
  type that compiles as native JSONB on PostgreSQL and JSON for existing SQLite
  test fixtures. PostgreSQL migration/storage remains JSONB; no existing tests
  were weakened. This is not a promise of production SQLite support.
- Initial migration-test SQL literal was changed to a bound JSON parameter;
  migration logic did not need a data-preservation workaround.
- Final fresh-UI integration and exact head checks are recorded before handoff.

## Exact next implementation unit

1. Confirm this schema PR's merge/upgrade status; fetch fresh dev and recheck the
   migration head rather than assuming 0087 is still the newest revision.
2. Implement immutable content/window/recipient/part snapshots and acknowledged
   receipts in delivery_state; recheck authorization before each resumed part.
3. Add a validated job-to-run reference using existing job JSON to preserve the
   original period/generation across job retries, including midnight rollover.
4. Add publication serialization and conservative unknown/in-flight handling;
   acknowledged sends must not repeat, uncertain sends must not be guessed unsent.
5. Test partial success, per-part resume, restart, checkpoint-save failure,
   revoked/foreign destinations, force/legacy cases and whole-suite regressions.
6. Update the planning branch's implementation tracker: prepared/in-review is
   not merged, and the full PRD-01 gate remains open.
