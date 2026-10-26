# Model deletion audit history

Historical source metadata check and proposed PostgreSQL acceptance scope. Not deployment evidence.

## Validation and remaining PostgreSQL acceptance

The inventory above was derived from successful mapper configuration and
compiled table metadata; it does not execute a DELETE or prove a deployed FK.
This report concerns declared deletion rules only. Historical Settings Platforms
checks did not validate these destructive SQLAdmin/FK paths and are tracked in
the phase record.

Before changing deletion policy, the sequential owner-local route must verify:

- Compare installed PostgreSQL constraints and current Alembic revision with the
  declarations (read-only first; no automatic migration/reset).
- ORM deletes with children loaded AND unloaded, plus direct/bulk SQL deletes.
- Platform → source → analytics/actions/receipts/seen; task-owned source removal;
  user → membership/credentials; workspace descendants; scenario/task SET NULL
  and provider/model deletion.
- RESTRICT cases with billing/ledger/dispatch evidence and initiated jobs;
  transaction rollback leaves parent and all protected children intact.
- ModelType/permission association mismatch and assigned-role refusal.
- SQLAdmin multi-select partial-commit/error behavior on a controlled test fixture.

Run these only against the existing approved local test target through the
repository's sequential primary test runner. No production deletion, reset,
new schema, migration or deployment is authorized by this report.
