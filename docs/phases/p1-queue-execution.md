# P1 — Truthful queue execution (gate PRD-05)

Goal: no handler starts after claim loss; recovery cannot duplicate an
unresolved external effect; execution outcome remains truthful.
Delivered: claim renewal, preflight, heartbeat, atomic-trigger slices;
source-aware explicit-run admission (PRs #192, #193 — bounded slices merged;
raw new-input wakeups retained). Queued-occurrence invalidation and the real
PostgreSQL race/rollback/ASGI acceptance are READY for review (P1-T02, P1-T01).

## Tasks

- [ ] P1-T01 (AUD-PR193) — prove source admission on PostgreSQL across
  concurrent connections, rollback and ASGI workspace scope; accept: real
  two-session race admits exactly one job, aborted admission leaves no
  occupancy, another workspace cannot run the task; depends: none;
  owns: `tests/jobs/test_task_run_now.py`; owner: codex; status: READY.
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
  Awaiting coordinator review; Codex marks DONE and deletes the registry row.

- [ ] P1-T02 — select and close the next bounded recovery or admission gap;
  accept: chosen gap verified against current code, fix keeps claim-loss,
  duplicate-effect and truthful-outcome invariants, focused tests via R10;
  depends: none; owns: `app/models/managers/agent_task_manager.py`,
  `app/models/managers/job_manager.py`, `docs/AGENT_TASKS.md`,
  `tests/jobs/test_task_pending_occurrence.py`; owner: opencode; status: READY.
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
  Semantics recorded in `docs/AGENT_TASKS.md`. Awaiting coordinator review;
  Codex marks DONE and deletes the registry row.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
