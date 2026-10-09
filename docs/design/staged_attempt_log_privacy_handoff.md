# Staged attempt-count warning privacy — owner handoff

Baseline: fresh dev `3373a1eaa14a24e9e199e87d6021d6311e05cc45` (2026-10-09).
Branch `fix/staged-attempt-log-privacy`; code `9bb566a`. PREPARED, NOT MERGED;
local acceptance/deployment pending. A new explicit owner merge command is
required; previous PR #23/#24 approval is not reused.

## Scope and preserved behavior

Only `_count_failed_staged` failure warning in `app/jobs/handlers.py` changes.
Previously it interpolated storage exception text. Now it emits WARNING event
`staged_attempt_count_failed`, a positive signed-64-bit integer `source_id`
(otherwise None), and fixed category `storage_operation_failed`. No exception
object, class name, string, SQL, content hash or traceback is passed to logging.
The category describes the operation, not a proven cause. Detailed diagnostics
are deliberately removed, not stored in a new alternate log.

Hash filtering/order/duplicates, record_attempts arguments, storage count,
transaction, zero fallback, session cleanup and cancellation remain unchanged.
A lost count retains the existing retry behavior; this package neither increments
rows differently nor deletes staged content. Hash extraction/new_session/close
exceptions retain their existing propagation behavior, not a new never-raise
promise. No retry/backoff/checkpoint/tenant/cost changes.

Do not redo PR #20 dispatcher privacy or PR #23 retirement warning. Owner UI,
runtime guard and collect partial/auth_required results are untouched. Remaining
collect/analyze/prune logs, provider/ORM/framework logs, successful summaries and
persisted errors remain outside scope. No global privacy/production acceptance.

## Prepared tests and observed evidence

Nine cases in `tests/test_staged_attempt_log_privacy.py`, actual selected helper
AST with mocked storage; WRITTEN, NOT RUN:

- Safe LogRecord message template/args/category and source correlation; no
  exc_info/exc_text/stack_info, raw secret/hash or exception string/class name.
- Dangerous non-stringifiable exception and unexpected identifiers; valid bounds,
  strings/bool/None/nonpositive/oversized IDs.
- Zero fallback, transaction exit with original exception, session cleanup.
- Success count and exact filtered hash order/duplicates; no-hash early return.
- Transaction-entry failure, cancellation propagation and close-error propagation.

Static AST parsing and comparison of every unaffected top-level source node were
performed: no other helper/handler/guard changed. Test-file AST parsing and
new-file line-length/trailing-whitespace inspection were performed. No application
imports, tests, formatter, DB/migrations, provider or messenger calls executed.
Existing tests were not removed or weakened; mocked helper tests are not full
module or PostgreSQL acceptance.

Prior PR #23 (head `a332a98`) and PR #24 (head `79aa1a8`) now have observed
successful GitGuardian Security Checks and Kilo Code Review. PR #23 review threads
and reviews were empty at inspection. These are GitHub check outcomes, NOT
application-test evidence for either package or this branch.

## Exact local commands

From repository root, with configured common POSTGRES_URL and a distinct
DB_TEST_SCHEMA, not working DB_SCHEMA (effective default public):

```bash
source .venv/bin/activate
python -m scripts.setup_test_db --check
python -m pytest --no-cov -q tests/test_staged_attempt_log_privacy.py tests/test_staged_retirement_log_privacy.py tests/test_dispatcher_log_privacy.py tests/test_returned_job_failures.py
python -m pytest --no-cov -q tests/test_dispatcher_log_privacy_db.py tests/test_returned_job_failures_db.py tests/test_job_notifications.py tests/test_job_status.py
python -m pytest -q
```

Optional standalone helper check without pytest/application startup:
`python tests/test_staged_attempt_log_privacy.py`.
No reset/drop/stamp/migrations; no concurrent pytest processes on one schema.
Expected: selected warning contains only safe operation/category/source metadata;
attempt-count calls/counts/fallback/cleanup remain unchanged and existing outcomes
and notifications remain intact. Record exact tested head, commands/results and
any failures; do not count historical PR #20 passes as new acceptance.

## Synchronization and continuation

Parallel PR #22 `ai/identity-permissions-boundary` was still open at inspection,
head `e46301a`; it overlaps this lane only in the implementation tracker and
next-session list. Preserve BOTH journal entries and all identity/owner work
when syncing; do not merge that branch under this task. Refresh dev/open PR heads
before the next stage and before any authorized merge.

Next: owner/local focused/full-suite results and this PR review; merge only on
new explicit instruction. After integration, next separate candidate is the
`handle_prune` stale-collected-items warning, or one coordinated collect path
preserving partial outcomes. Do not broaden this package or activate senders.
