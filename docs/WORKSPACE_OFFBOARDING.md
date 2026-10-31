# Workspace offboarding

The console's **Удалить с аудитом** action is the only interactive workspace
removal path. It is reserved to a freshly rechecked active platform superuser;
admin/operator grants and workspace membership cannot delegate it. Legacy
SQLAdmin CRUD/bulk DELETE is disabled. Select exactly one workspace.

## Preview, policy and confirmation

GET renders a read-only setup page. A CSRF-protected preview POST binds a signed
review to the actor, workspace identity/version, allowlisted reason, retention
policy and operation UUID for 15 minutes. The final POST requires its session
CSRF token, review token and explicit irreversible-deletion checkbox. An actor
change, expired signature or changed workspace settings requires a new preview.

The preview shows retained/expired task-audit counts and protected references.
Default `preserve` refuses deletion while any task audit exists.
`expire_elapsed` explicitly permits removal only of records older than the
workspace plan's current retention period. Retained evidence always blocks;
unknown/unlimited retention never implies expiry. More than 5000 expired events
requires a separate normal retention pass first. Financial/other restrictive
FKs block deletion. No archive or implicit expiry of retained audit is performed.

The reason is one of `owner_request`, `duplicate`, `contract_ended`,
`test_completed`. No free-form comment, secret, workspace content or raw request
is copied to the permanent journal.

## Transaction and concurrency

Execution rechecks active SU and holds a share lock on its user row, locks the
workspace FOR UPDATE, then rechecks review version, retained audit and protective
references. This serializes deletion competitors and conflicts with new FK
references. Expiry of eligible audit, workspace deletion and success receipt
share one transaction. A failure rolls all three back. A competing review for a
deleted workspace is a conflict. The same still-valid signed review returns its
existing operation receipt, with one unique event UUID and workspace identity.

`workspace_offboarding_events` is a platform journal without actor/workspace
foreign keys, so evidence survives deletion of either subject. It stores only
numeric IDs, UTC time, operation UUID, allowlisted reason/policy, number of expired
audit rows and `succeeded`. UPDATE, DELETE and TRUNCATE are rejected by database
triggers. The application exposes no purge or edit method. Minimal journal
records are retained indefinitely; any later schema retirement requires a
separate reviewed data-preservation decision.

Read/export requires fresh SU at execution. The HTML view shows the latest 100
entries; CSV is bounded to 1000. Other actors are denied even with explicit
catalog VIEW/EXPORT grants. The journal is not a billing receipt.

## Schema and verification

Alembic revision `0119` follows `baseline_0118`. It adds the journal, immutable
guards and its access-catalog reference rows without widening role grants.
Application code checks the active guard definitions before journal access or
workspace deletion: metadata-created tables without guards are insufficient.
The existing tenant-scoped task-admission audit allowlist/FK remains unchanged.

Public migration application is separate from repository acceptance and is not
implied by tests or the task merge. Keep old code available for rollback before
application. A normal downgrade refuses a non-empty permanent journal; do not
remove audit evidence to make rollback pass. Empty downgrade is reversible.

Reproducible owner-local validation (load the private environment without printing
credentials; require exclusive local PostgreSQL test-schema use):

```sh
python -m scripts.verify_p11_t29_offboarding --plan
python -m scripts.verify_p11_t29_offboarding --execute
```

The runner explicitly prepares the P11-T28 reference permission before pytest,
then validates only localhost `social_manager/test_schema`, holds the shared
advisory lock, inventories test/public schema/reference state, upgrades 0119,
runs focused PostgreSQL/concurrency/HTTP/Safari checks and cleans synthetic rows.
A narrowly guarded `-x p11_t29_test_teardown=1` downgrade permits removal of the
owned disposable journal only on that local test target. Production has no
runtime bypass of journal immutability. Final inventories must match the prepared
baseline; pytest preflight remains read-only.
