# Digest job binding and guarded delivery integration

Status: **PREPARED IN PR #12; NOT MERGED, DEPLOYED OR ACCEPTED**.
Continuation baseline: dev `081165a7aac1cbb5b2f093f6ead20a89e1ce28ec`.
PR #12 was synchronized with that baseline before this continuation.

## Implemented in the review branch

- Default-off rollout: the process environment must explicitly contain
  `DIGEST_CHECKPOINT_DELIVERY_ENABLED=1`. Merging does not activate HTTP delivery.
- Dispatcher overwrites payload identity with authoritative Job columns.
  Client payload run IDs/generations are not used as resume references.
- Server-owned `Job.result.digest_delivery` reserves the original period/window,
  task and normalized request. Snapshot creation and binding of its run/generation
  commit in ONE transaction. The standalone snapshot factory API remains available.
- A PostgreSQL session advisory lock serializes jobs of one schedule before a
  summary charge. Manual jobs have distinct identities. Lock backend identity,
  job attempts/started_at fencing and a periodic lease heartbeat are checked.
- `build_started` is committed before summary execution. A crash after that mark
  without a committed snapshot needs reconciliation, not another model call.
  Actual known cost is retained on Job if binding fails; successful binding
  transfers that cost to DigestRun without counting it twice. Unknown is NULL,
  not proof of a free call. This is NOT a billing-grade reservation ledger.
- Bound retries load the exact owned run/window/generation; they do not aggregate,
  summarize, rerender, recalculate today's window or change frozen destinations.
  Changed task parameters/source availability cause a safe stop.
- Publisher uses the existing locked checkpoint store and send_part contracts:
  exact HTML parts, committed pre-HTTP intent and immediate durable outcome.
  Sent parts are skipped; in-flight/uncertain parts are NEVER blindly replayed.
  A revoked binding is blocked. Independent authorized targets may still finish.
- Only all-confirmed parts produce successful Job/Notification completion.
  Structured counts expose partial, blocked and uncertain states. Final job writes
  are claim-fenced: an old worker cannot mark a newer claim done/failed.
- The legacy builder refuses checkpoint-managed scheduled windows/reservations,
  including after flag rollback. With the flag on, direct old builder calls are
  refused: use a claimed digest job rather than bypassing the new publisher.

## Conservative recovery policy

1. Retry an ORIGINAL pending job only. Do not create another factory snapshot,
   copy a reference through payload, reset receipts, delete failed jobs, or set
   force_refresh to bypass a collision.
2. Missing original references on old retries, existing legacy scheduled windows,
   changed requests and force_refresh are rejected for operator review.
3. In-flight/uncertain means the messenger MAY have accepted the message. Inspect
   actual delivery evidence before any separately authorized new-generation send.
   This PR deliberately does not ship a force/reconciliation UI or automatic
   reset operation. Terminal blocked targets also need explicit operator action.
4. There is 0.6-second pacing between requests in one publisher; known rejections
   can use existing queue exponential backoff. HTTP 429 is terminal for operator
   delay because the current part transport does not expose verified Retry-After.
   This is NOT a distributed per-bot/per-destination rate limiter. Start with one
   worker; shared-target, shared-token and other sending paths need rate admission
   review before multi-worker/live activation.
5. If reservation occurred before midnight but no snapshot exists, today's
   aggregator cannot reconstruct historical ranges. Window drift is rejected
   BEFORE the summary call, not silently relabeled as the original date.
6. Existing successful-job cleanup/retention policies are unchanged. Frozen
   receipts remain on DigestRun, but Job references can be pruned by existing
   cleanup; preserve original jobs during recovery/acceptance. Retention and a
   complete event/cost audit remain separate production gates.

## Validation evidence for THIS continuation

Executed locally: 8 pure stdlib outcome tests; Python compilation, AST and
trailing-whitespace checks for the changed Python files.

Prepared but NOT executed here: 16 PostgreSQL integration cases in
`tests/test_digest_job_delivery.py`, and reruns of the existing component,
tenancy, dispatcher and full suites. This sandbox has no PostgreSQL, pytest,
SQLAlchemy or repository checkout; dependency installation/clone failed.
The prior agent's 250-test factory checkpoint is historical evidence ONLY,
not a result for the new integration. No live HTTP/LLM calls or migrations ran.

## Owner test procedure (isolated database ONLY)

Use the project's normal dependency/environment setup. Set TEST_POSTGRES_URL
and DB_TEST_SCHEMA to a dedicated disposable test database/schema as required
by scripts/setup_test_db.py and tests/conftest.py. Never point tests at the
working/production database. Do not publish real bot credentials in logs.

```bash
# Pure tests can run without application dependencies or a database.
python tests/test_digest_delivery_outcomes.py

# Requires installed project dependencies and isolated PostgreSQL.
python -m pytest -q tests/test_digest_delivery_outcomes.py tests/test_digest_job_delivery.py tests/test_digest_snapshot_factory.py

# Then run the existing delivery/tenant/dispatcher regressions and full suite.
python -m pytest -q
```

Keep the new flag unset in working deployments until these checks pass. Tests
set it with monkeypatch and mock all new HTTP/model calls. Add staging fault
injection for receipt commit failure, backend loss, reaping during HTTP, changed
requests/permissions, and concurrent workers before accepting production use.

## Deployment / rollback order

- Owner-approved merge is separate from deployment. No automatic merge or
  production DB change is included in this continuation.
- Inspect the actual database migration revision; migration 0087 must be applied
  by one authorized migrator BEFORE updated ORM code is started. A merged file
  does not prove the database was upgraded. Back up and validate staging first.
- Drain/stop ALL old digest producers and workers before enabling the flag in
  all relevant processes. Do not run mixed old/new publishers on the same run.
- Enable explicitly only after isolated tests and staging acceptance. Use claimed
  jobs; direct legacy builder calls intentionally fail when enabled.
- Roll back the FLAG, not delivery evidence: bound/reserved jobs remain blocked
  from old sending paths. Do not clear JSONB state or pretend NULL means unsent.
- PRD-01 end-to-end acceptance, PRD-03 complete spend accounting, PRD-05 general
  queue/scheduler correctness and PRD-06 retention are NOT closed by this PR.
