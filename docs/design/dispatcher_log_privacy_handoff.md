# Dispatcher log privacy: owner handoff

Status: PREPARED IN BRANCH `ai/dispatcher-log-privacy`; NO PR CREATED OR MERGE.
Baseline dev `1e908b2d55bae6ab2c71271321d2986a921ae9e4` (2026-10-09), including
owner merge of PR #19 as `f0a4510` and the subsequent chat/notification UI changes.
The owner will create/review the PR. No UI or owner runtime guard change.

## Bounded inventory and changes

Selected surface: `app/jobs/dispatcher.py` only. Baseline dispatcher emitted full
success results, retry exception text, terminal exception tracebacks, notification
write tracebacks and worker-iteration tracebacks. Exceptions also entered the
failure notification message.

Prepared changes:

- Replace dispatcher-owned result/exception logs with fixed event names, positive
  integer job/tenant/task IDs and an allowlisted job type. Unexpected identifier
  shapes are omitted, unknown types become `unknown`, not formatted as content.
- Preserve log severity and distinguish completed jobs, declared result failures,
  scheduled retries, terminal failures, lost claims and notification write errors.
- Error categories are fixed: timeout, connection_error, io_error, value_error,
  runtime_error, unexpected_error. Declared returned failures keep the existing
  bounded `result_outcomes` code, such as invalid_structured_output.
- Do not log exception args/messages/repr/custom class names, results/payloads,
  traceback or stack information from these dispatcher calls.
- Failure notification uses a fixed Russian template and the existing task link;
  no raw exception is copied into its title/message. Its error argument remains
  accepted for compatibility but is not interpolated. Unknown job types are bounded.
- Worker startup/stop and failed iterations use static events; polling behavior,
  cancellation and retry sleep are unchanged.

No regex-based attempt to redact arbitrary messages: omit unsafe text at this
specific boundary. Logs still carry operational metadata; they require appropriate
access and retention. No new external telemetry/export or logging dependency.

## Behavior deliberately preserved

Handler invocation, tenant scope, claimed-column payload identity, Job/AgentTask
writes, exception retry/backoff, inline no-retry, checkpoint finalization/claim
fences, flag rollback routing and cost (including unknown versus explicit zero)
are unchanged. No schema/model/queue-claim/provider/fallback/permission changes.

**Audit columns are NOT sanitized by this package.** Ordinary exception text still
reaches Job.error and AgentTask.last_error; success results and existing successful
notification summaries still contain authorized workspace content. Existing rows
are not rewritten or removed. Web/admin displays still need a separately reviewed
privacy/access policy. Do not infer that all errors, stored data or process logs
are secret-free.

The inventory also identified raw exception logs in `app/jobs/handlers.py`
(collection/analyze/staging/retention). Those and provider/ORM/HTTP/framework logs,
upstream tracing and the owner's runtime guard logs remain OUT OF SCOPE. This is
not a whole call-chain/fleet-wide log filter. No general privacy gate is closed.
Removing traceback diagnostics is intentional; investigate via safe IDs and the
existing appropriately restricted task/job records, not by restoring raw logs.

## Verification

Actually executed because of concrete leak/control-flow concerns:

- **13 actual-source privacy tests PASSED**: successful/private result, raw retry
  and terminal errors, declared failure/cost, persistence failure, skip, claim
  loss/checkpoint retry refusal, bounded categories and IDs, real notification
  function with mocked DB writer, and worker error/cancellation.
- **25 existing actual-source/mocked outcome regressions PASSED** against the new
  dispatcher. These are fresh runs, not new PostgreSQL/full-suite evidence.
- Compilation, AST and whitespace checks passed. No black/isort run is claimed.

**2 PostgreSQL cases PREPARED, NOT EXECUTED** in
`tests/test_dispatcher_log_privacy_db.py`: terminal versus retry. They exercise
real Job/AgentTask writes and DB-only notification persistence, with mocked
handler and a guard against messenger sending. Capture assertions apply ONLY to
records owned by the dispatcher logger; DB audit text is explicitly unchanged.
Pytest/SQLAlchemy/FastAPI/httpx/PostgreSQL are unavailable in the authoring sandbox.
No DB, migration, live handler/provider or messenger call ran here.

## Owner/local-agent commands

Use the project .venv at repository root. Common POSTGRES_URL + distinct
DB_TEST_SCHEMA remains the configuration. Do not share one test schema between
parallel pytest processes. No reset/drop/stamp or second database is required.

```bash
python tests/test_dispatcher_log_privacy.py
python tests/test_returned_job_failures.py
python -m scripts.setup_test_db --check
python -m pytest --no-cov -q tests/test_dispatcher_log_privacy.py tests/test_dispatcher_log_privacy_db.py tests/test_returned_job_failures.py tests/test_returned_job_failures_db.py tests/test_job_status.py
python -m pytest -q
```

Expect safe dispatcher log events and fixed failure notifications, unchanged
failure audit/known cost and retry decisions, no live messenger/provider call.
Record tested revision/commands/results; do not attribute prior owner-reported
passes to this new branch. Formatting should be checked locally before merge.
No application CI runs are claimed for this branch (no PR was opened here).

## Next session and overlaps

Before creating the owner's PR or doing more work, compare fresh dev and this
branch; dispatcher overlaps with future queue/identity work and must be coordinated.
Keep owner chat/notification template changes intact. Update the tracker after
actual merge, distinguishing merged from deployed/accepted.

Next bounded task: inventory remaining handler logging and select one path;
alternatively audit task-result presentation after reserving UI files with the
owner. Fail-closed identity, queue leases/atomicity, atomic memory writes, spend
ledger, retention and deployment recovery remain separate important packages.
No PR/merge or sender activation is authorized by this handoff.
