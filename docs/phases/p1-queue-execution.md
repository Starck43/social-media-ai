# P1 — Truthful queue execution (gate: queue execution stays truthful about live and historical state)

Goal: no handler starts after claim loss; recovery cannot duplicate an
unresolved external effect; execution outcome remains truthful.
Delivered: claim renewal, preflight, heartbeat, atomic-trigger slices;
source-aware explicit-run admission (PRs #192, #193 — bounded slices merged;
raw new-input wakeups retained). P1-T01 admission acceptance is complete.
P1-T02 remains in progress after the implementation audit found definition
changes that leave a queued snapshot stale.

## Tasks

- [x] P1-T01 (AUD-PR193) — prove source admission on PostgreSQL across
  concurrent connections, rollback and ASGI workspace scope; accept: real
  two-session race admits exactly one job, aborted admission leaves no
  occupancy, another workspace cannot run the task; depends: none;
  owns: `tests/jobs/test_task_run_now.py`; owner: codex; status: DONE.
  The recorded blocker was a test-side deadlock, not an admission defect:
  the monkeypatched lock was installed before the rollback pre-check, so the
  pre-check became call #1 and parked on `release_first`; the final `gather`
  had no timeout. Fixed at `765c91b` (stub installed only for the two-session
  race, gather bounded). Acceptance evidence at dev `765c91b`, primary
  OpenCode test route, real PostgreSQL, `test_schema`, sequential, no
  reset/migration/provider calls:
  `test_source_admission_serializes_real_postgres_sessions_and_rolls_back` and
  `test_web_run_now_cannot_cross_workspace_scope` — 2 passed, 0 failed,
  0 skipped, exit 0. This is the bounded race/rollback and workspace-scope
  contract, not a universal execution mutex, production timing or kill proof.
  Accepted by Codex at dev `dab3b5c7d5018dd21d7334e2f1b13528c6221964` after
  reviewing the manager, both route paths and the recorded real-PostgreSQL
  evidence. No broader execution-mutex or production-kill claim is made. The
  resolved registry row was removed.

- [x] P1-T02 — invalidate queued task occurrences on definition changes, pause
  or deletion; accept: execution-field and explicit source-membership changes
  invalidate replaceable pending Jobs atomically and serialize with admission;
  unchanged edits preserve queued work, running/terminal/unconfirmed rows retain
  their recovery policy; fix keeps claim-loss, duplicate-effect and
  truthful-outcome invariants, focused PostgreSQL tests via R10;
  depends: none; owns: `app/models/managers/agent_task_manager.py`,
  `app/models/managers/job_manager.py`, `docs/AGENT_TASKS.md`,
  `tests/jobs/test_task_pending_occurrence.py`,
  `tests/jobs/test_task_run_now.py`; owner: codex; status: DONE.
  Selected gap: a queued occurrence snapshots the task definition at admission,
  but editing `job_type`/`cron_expr`/`payload`/`agent_scenario_id`/`next_run_at`,
  deactivating or deleting the task left that row pending, so it later ran the
  old plan; deletion was worse because the Job FK is `ON DELETE SET NULL` and
  the row survived without its task. Workspace-level scheduled types could also
  add one pending copy per tick while their earlier occurrence was active.
  Fix at `6364818`: pending rows are removed in the same transaction that
  invalidates them, workspace-level scheduled admission returns the existing
  busy receipt, and `JobManager.delete_pending_for_task` keeps running,
  terminal and unconfirmed-outcome rows. Manual `@once` re-runs still replace
  their unfinished row, so a task keeps at most one queued or claimed
  occurrence. Claims, duplicate-effect policy and terminal history are
  unchanged; no migration.
  Evidence at `6364818`: `tests/jobs/test_task_pending_occurrence.py` 6 passed;
  `tests/web/test_web_task_edit.py` 8 passed (14 together, 0 failed/skipped);
  `tests/cli/test_cli_selection_flags.py` 20 passed (isolated, no DB).
  Semantics recorded in `docs/AGENT_TASKS.md`. Re-audit at dev
  `dab3b5c7d5018dd21d7334e2f1b13528c6221964` rejected that implementation: pending
  invalidation only compares `job_type`, `cron_expr`, `payload`,
  `agent_scenario_id` and `next_run_at` (plus deactivation). The admitted job
  snapshot also captures `trigger_type`, `trigger_config`, `action_type`,
  `rate_limit_per_hour`, `cooldown_seconds` and `dry_run`; changing these can
  leave an old pending snapshot runnable. Source membership changes through
  `AgentTaskManager.set_sources` also do not invalidate pending rows, although
  source IDs are part of the effective snapshot. Existing focused tests do not
  cover these edits. Return to implementation; add invalidation and focused
  coverage before READY. Recorded passing tests remain evidence for the
  implemented cases only, not these uncovered paths.
  Follow-up implementation closes all snapshot-field and actual source-membership
  changes, including bulk mutation paths. Mutation and admission take the same
  workspace gate before task row locks; source writes and pending cleanup share
  the caller's transaction, including rollback. Same-value/source-set edits keep
  queued work; running, terminal and unconfirmed outcomes retain their policy.
  No migration. Independent implementation and test review found no blocker.
  Validation used isolated base `92dcb948de24c55ab82dccdba81e792945b5ac48`
  plus frozen manager blob `de3059c42d21a03ba37c3486e155ff2d46134d0a`
  and pending-occurrence test blob `0d86150edb32b57877d425c351f0df236c2b16ce`.
  Primary OpenCode route, real PostgreSQL `test_schema`, sequential, no schema
  changes: 33 pending-occurrence, 17 task-activity and 8 web-edit tests passed.
  The initial regression batch exposed stale run-now fixtures/assertions;
  corrected cleanup, explicit background-launch stub, current permission/link
  and progress/metric contracts were reviewed independently. Final run-now blob
  `54eb5ea6b565f8debaa176ebf9b368a46c288e71`: 20 collected, 20 passed,
  0 failed/skipped. CLI selection flags: 20 passed in its isolated no-DB run.
  These are combined focused results (98 distinct tests), not a single green
  full-suite run. The separately tracked historical queue-label defect is P1-T03.
  Accepted by Codex on 2026-10-22 at dev
  `c9ccf9d4a2328ec2c707b71f45cd7541375daa97`: committed file blobs match the
  reviewed and tested snapshots; the later integration changes were checked
  for relevant overlap. The resolved registry row was removed. At this
  acceptance, P1-T01 remained accepted; P1-T03 was still open and the phase
  was not closed.

- [x] P1-T03 — distinguish a live queued Job from the task's historical queued
  receipt; accept: task list and detail show no live-queue claim when the last
  pending Job was invalidated, while real pending/running Jobs still have their
  current state and historical trigger metadata is retained; tenant and
  permission boundaries stay intact, focused projection tests via R10;
  depends: P1-T02; owns: task status projections in `app/web/tasks.py`, task
  templates and focused web tests; owner: codex; status: DONE.
  Admission records `last_status="queued"`; invalidation can remove the pending
  Job without changing that historical receipt. At `f55aa2a2c7418e29a642324bf8753bbc53880ad3`,
  the task list reports live pending/running state only from `running_jobs`,
  while both list and detail label an orphaned queued receipt as historical.
  An unconfirmed-outcome warning and a live Job state remain independently
  visible. Tenant query scope, permissions and trigger metadata are unchanged;
  no migration.
  Acceptance evidence at exact dev commit `f55aa2a2c7418e29a642324bf8753bbc53880ad3`:
  OpenCode/River no-DB run of
  `tests/unit_web/test_task_detail_progress.py::test_task_run_panel_keeps_legacy_status_and_scope_when_task_has_no_job`,
  `tests/unit_web/test_task_detail_progress.py::test_task_list_row_reports_queue_state_only_from_a_live_job`,
  and `tests/jobs/test_queue_recovery_admission.py::ProjectionAndRetentionTests::test_ui_shows_unknown_and_does_not_offer_blind_retry`
  — 3 passed, 0 failed/skipped. The DB-backed endpoint assertion in
  `tests/jobs/test_task_run_now.py` was updated but not part of this final
  focused run; no database changes were made. A broader exploratory run at the
  pre-acceptance base had 33 passed and 3 unrelated dispatcher-policy failures;
  its targeted projection node passed on the accepted commit. Accepted by
  Codex after reviewing the exact diff and test evidence.

## Phase approval

- [x] PHASE DONE — approved by: codex, date: 2026-10-25, dev SHA: f55aa2a2c7418e29a642324bf8753bbc53880ad3
