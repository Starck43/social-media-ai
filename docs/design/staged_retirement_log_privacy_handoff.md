# Staged retirement warning privacy — owner handoff

Baseline: fresh dev `a88cd3001f184531d68a21e93ddff1aa9cb39e4c` (2026-10-09),
after PR #20/#21. Code commit: `be3f67c`; tests/handoff: `a332a98`.
[PR #23](https://github.com/Starck43/social-media-ai/pull/23) merged into dev as
`98e3aadbb27bb2119c5e4876149345908afcc425` on explicit owner instruction.
Fresh dev/head/diff checked; integration is not acceptance or deployment.
GitGuardian Security Checks and Kilo Code Review were still in progress at the
post-merge inspection. Follow-up inspection observed both checks successful
for head `a332a98`; docs PR #24 head `79aa1a8` also succeeded. PR #23 reviews and
threads were empty. This updates GitHub evidence only; all helper cases remain
unrun here and application/full-suite acceptance is still pending.

## Selected surface and inventory

Only `_retire_staged` in `app/jobs/handlers.py` is changed. Its failure warning
previously formatted the storage exception, potentially exposing SQL, connection
credentials or raw content. It now logs WARNING event `staged_retirement_failed`,
a positive signed-64-bit integer `source_id` (otherwise None), and static category
`storage_operation_failed`. No exception object, message, class name, traceback
or content hash is passed to logging. This deliberately removes detailed error
diagnostics; the category describes the operation, not a proven failure cause.
No new dependency on the dispatcher or refactor of PR #20.

Remaining inventory, NOT fixed by this package:

- `_count_failed_staged`: subsequently prepared in a separate branch, NOT merged;
  see [attempt-warning handoff](staged_attempt_log_privacy_handoff.md).
- `handle_collect`: per-source raw exception and traceback; returned error strings
  and partial/auth_required outcomes stay unchanged.
- `handle_analyze`: staged-processing and outer per-source exception/traceback logs;
  guard reason and existing metadata logs are also outside scope.
- `handle_prune`: raw sweep exception warning.
- `_load_task` / `_resolve_sources`: existing identifier/source-list logs untouched.
- Provider, ORM, framework logs, Job.error/AgentTask.last_error and successful
  notification summaries remain outside scope. No global privacy claim.

Deletion/hash selection, transactions, return-zero fallback, cancellation and
session-close behavior are unchanged. Session creation, hash extraction and
close exceptions have their existing propagation behavior; this package does
not attempt to make those paths never raise. No collect/analyze result shape,
retry policy, UI, runtime guard, migration, DB or messenger change.

## Prepared checks — NOT run

Seven new unittest cases in `tests/test_staged_retirement_log_privacy.py` compile
only the actual helper AST, with mocked storage and hash extraction. They cover
LogRecord template/args and absence of exc_info/exc_text/stack_info; raw secrets,
non-stringifiable exception and custom class name; invalid ID types/ranges and
valid bounds; zero fallback/cleanup, success/hash forwarding, no-hash early return
and cancellation propagation. These are not full-module or real-DB acceptance.
No existing test was removed or weakened. No tests were executed in this session.
No new pass counts, formatting checks or PostgreSQL evidence are claimed.

Owner/local agent, from repository root:

```bash
source .venv/bin/activate
# Use the existing common POSTGRES_URL and a distinct DB_TEST_SCHEMA.
# DB_TEST_SCHEMA must not equal working DB_SCHEMA (effective default: public).
python -m scripts.setup_test_db --check
python -m pytest --no-cov -q tests/test_staged_retirement_log_privacy.py tests/test_dispatcher_log_privacy.py tests/test_returned_job_failures.py
python -m pytest --no-cov -q tests/test_dispatcher_log_privacy_db.py tests/test_returned_job_failures_db.py tests/test_job_notifications.py tests/test_job_status.py
python -m pytest -q
```

The isolated new helper file can also be run without pytest/application startup:
`python tests/test_staged_retirement_log_privacy.py`.
Do not reset/drop schemas or run concurrent pytest processes on one test schema.
Expected: selected warning contains safe event/category/source correlation only;
retirement counts, fallback and cleanup stay unchanged; existing outcome and
notification regressions remain intact. Record exact tested SHA, commands and
results; historical PR #20 checks are not acceptance for this change.

## Next continuation

Fetch fresh dev and open PRs before editing overlapping files. Open PR #22 has
documentation overlap in the implementation tracker and next-session list, but
no application/test overlap with this package. Do not merge it under this task
or overwrite its separate identity work; retain both journal entries on sync. Collect owner/local
acceptance evidence. `_count_failed_staged` warning is now prepared separately,
NOT merged. Review that package/local results before taking a new path; preserve
partial outcomes. Do not redo dispatcher-owned logs; do not touch owner UI/runtime guard.
Identity, queue, atomic memory and budget-ledger work remain separate branches.
No sender activation, migration or live external operation is authorized here.
