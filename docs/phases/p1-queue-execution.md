# P1 — Truthful queue execution (gate PRD-05)

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

- [ ] P1-T02 — select and close the next bounded recovery or admission gap;
  accept: chosen gap verified against current code, fix keeps claim-loss,
  duplicate-effect and truthful-outcome invariants, focused tests via R10;
  depends: none; owns: `app/models/managers/agent_task_manager.py`,
  `app/models/managers/job_manager.py`, `docs/AGENT_TASKS.md`,
  `tests/jobs/test_task_pending_occurrence.py`; owner: opencode; status: IN_PROGRESS.
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
  `dab3b5c7d5018dd21d7334e2f1b13528c6221964` does not accept the task: pending
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

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
