# Returned job failures: owner handoff

Status: PREPARED, NOT MERGED OR DEPLOYED. Baseline dev `be333fe` (PR #17 merged).
Branch: `ai/returned-job-failure-outcomes`. This is a bounded queue-outcome fix,
not completion of the general queue/scheduler production gate.

## Changed behavior

Previously a handler returning `{"status": "failed", ...}` reached mark_done,
recorded an OK task result and produced a success notification. Learning and
reflection validation failures from PR #17 therefore looked successful even
though no memory mutation was accepted.

For a non-checkpoint handler's explicit `status=failed` result:

- mark the Job failed and finished; record failed AgentTask status;
- do not automatically retry/replay it, even when allow_retry was True;
- retain finite nonnegative reported USD cost, including explicit zero;
- leave absent/malformed cost unknown instead of coercing it to free usage;
- persist a minimal audit result: status, whitelisted static error code and cost;
- do not copy arbitrary error text or extra provider/customer payload into this
  failure audit, local returned-failure log or notification;
- issue a failure notification, never a success notification.

Failure-persistence exceptions also do not enable a retry of the declared
failure. This does not guarantee that a failed DB write retained cost/result.

Existing exception backoff remains for handlers that raise; inline exceptions
remain terminal. Legacy result dictionaries with numeric error counters are
not interpreted as declared failures. Skips remain completed without a success
notification. Unknown statuses remain legacy behavior, not a new full schema.

Checkpoint digest jobs retain their own finalizer/claim/retry rules, including
original-reference routing after flag rollback. No delivery receipts, frozen
snapshots, sender flag or force/uncertainty policy changed. No memory transaction,
identity/router, UI/template, migration or owner runtime guard change.

## Shared PostgreSQL: correction to prior handoff

Your common POSTGRES_URL is supported. Use the separate DB_TEST_SCHEMA already
configured for tests; a second database is not required. TEST_POSTGRES_URL is
an existing optional override, not a new requirement or something to provision.
The application defaults are DB_SCHEMA=public and DB_TEST_SCHEMA=test_schema.
The effective working and test schema MUST differ.

Existing conftest redirects URL/schema before importing application engines and
model metadata. Bootstrap already supports a shared database and avoids stamping
its working Alembic version table. This follow-up does not alter bootstrap or
config, inspect your actual DB, create/reset/drop a schema or run a migration.

Do not run parallel pytest sessions sharing one test schema. Schema separation
is not a security sandbox against a role with broad privileges, raw cross-schema
SQL or cross-schema cascade dependencies; it does not justify destructive work
on the working schema. No --reset/drop command is part of this test handoff.

## Actual checks

**25 standalone policy/mocked-source tests PASSED**:

```bash
python tests/test_returned_job_failures.py
```

They load the actual dispatcher/manager source with infrastructure imports
mocked and cover terminal failed results, cost/zero/unknown, task recording,
minimal redacted audit, notification type, inline outcome, exception backoff,
legacy counters/skips, checkpoint finalizer/reference routing and column-owned
payload identity. Compilation, AST and whitespace checks also passed.

**3 PostgreSQL persistence cases PREPARED, NOT EXECUTED**:
`tests/test_returned_job_failures_db.py` checks Job+AgentTask failure/cost,
unknown-provider error redaction and the inline final-row outcome. Pytest,
SQLAlchemy/httpx/asyncpg/PostgreSQL are unavailable in this sandbox. No live
provider, transport or DB calls ran. Owner's independent test results were not
supplied, so no full-suite or end-to-end acceptance is claimed.

## Owner commands

Run in the normal project environment with common POSTGRES_URL and your
isolated DB_TEST_SCHEMA. Confirm it is not the working DB_SCHEMA first.

```bash
python tests/test_returned_job_failures.py
python tests/test_ai_output_boundaries.py
python -m pytest -q tests/test_returned_job_failures.py tests/test_returned_job_failures_db.py tests/test_learn_cost.py tests/test_job_notifications.py tests/test_job_status.py tests/test_task_run_now.py tests/test_cli_direct.py tests/test_cli_task_run.py
python -m pytest -q tests/test_ai_output_boundaries_db.py tests/test_learning.py tests/test_digest_job_delivery.py
python -m pytest -q
```

Record the tested head, output and effective schemas (redact URL credentials).
Do not activate the checkpoint sender as part of this PR.

## Remaining limits

- Generic Job writes still lack attempt/claim CAS fencing. A stale worker can
  race a newer run; this PR does not establish concurrency/lease acceptance.
- Job and AgentTask outcome writes are separate transactions. Notification is
  best-effort, not a durable transactional outbox; crashes can leave a mismatch.
- Reported cost is not a complete per-attempt/reservation ledger. Unknown cost
  remains unknown in this result; existing spend summation is still incomplete.
- DB-persistence failure can lose audit/cost; generic exception logs retain the
  pre-existing raw-exception path. Redaction here is only the returned-failure
  success-of-persistence path, not a process-wide logging audit.
- Unknown result statuses and partial collection failures are not redesigned.
  A future versioned handler-result protocol can cover them without guessing.
- Manual reruns may repeat side effects; terminal status is not an exactly-once
  guarantee, an automatic recovery procedure or approval to reset receipts.

PRD-03/05/06 remain OPEN. Merge and activation require separate approval. Every
commit contains an English owner handoff with actual checks and pending work.
