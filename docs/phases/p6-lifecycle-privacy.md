# P6 — Lifecycle and privacy (gate PRD-06)

Goal: tenant data is isolated and auditable; deletion preserves evidence
required for unresolved effects and accounting.

## Tasks

- [ ] P6-T01 — data-retention rollout (code merged via PR #139);
  accept: deployment approved by owner, `ai_analytics` backed up if history
  matters, `DATA_RETENTION_MONTHS` confirmed (default 1 — first daily prune
  irreversibly deletes analyses older than cutoff), post-deploy verification
  recorded, then this row is removed; depends: owner deployment approval;
  owner: owner; status: BLOCKED — owner decision (2026-10-12): do not deploy yet

- [ ] P6-T02 — decide and implement one bounded retention, export, offboarding,
  credential-revocation or logging contract; accept: contract documented and
  implemented with isolation/audit evidence; depends: none;
  owns: per selection; owner: unassigned; status: TODO

- [ ] P6-T03 — add a workspace-admin-only action on the source page to delete
  only that source's raw staged `CollectedItem` rows; accept: explicit
  confirmation, server-side admin and CSRF checks, tenant/source scoped delete,
  refuse while collect/analyze work is pending or running, analytics and
  receipts untouched; add focused tests; no live DB cleanup; depends: none;
  owns: `app/web/sources.py`, `app/web/templates/web/source_detail.html`,
  `app/models/managers/collected_item_manager.py` and focused tests;
  owner: codex; status: IN_PROGRESS; evidence: diff review and `git diff --check`
  passed, compileall passed; focused pytest was not run because the OpenCode
  model quota was exhausted and the secondary Kilo task handoff was rejected.

## Phase approval

- [ ] P6-T04 — prevent source deletion from widening an explicitly scoped task
  to all active workspace sources; accept: deleting the final selected source
  atomically pauses or otherwise preserves the explicit task scope; genuinely
  all-active tasks retain their contract; tenant/permission checks and concurrent
  task edits are covered by focused PostgreSQL acceptance; depends: none;
  owns: source deletion manager/service, affected delete callers and focused
  tests; owner: opencode; status: READY (Codex review rejected `510f213` on
  2026-10-22; the blocker is addressed below). Confirmed at `a11f1f9`:
  source deletion removes m2m links but leaves the task active, while an empty
  link set means all active sources. See
  [social analysis audit](../audits/2026-10-18-social-analysis-web.md).
  Evidence: one delete path `app/services/collection/source_deletion.py`
  (`delete_source(session, source_id, tenant_id)`) — holds the source-admission
  lock, locks the source row and the linked task rows, deletes the row
  (analytics/m2m by FK cascade), and pauses the tasks whose *only* named source
  it was, in the same transaction, through `AgentTaskManager.update_by_id`
  (so a queued occurrence of the old plan is dropped) with a note naming the
  deleted source; tasks with other links and tasks with no links are untouched.
  Callers: `POST /app/sources/{id}/delete` (names the paused tasks in a second
  flash; lane refusal unchanged) and `DELETE /api/v1/sources/{id}` (resolves the
  row's workspace, then deletes inside that scope). Contract documented in
  `docs/API.md`. Tests: `tests/collection/test_source_deletion_scope_db.py`
  (7 passed — last-source pause, remaining-link survival, all-active untouched,
  already-paused untouched, queued occurrence dropped, foreign workspace
  reported missing, concurrent editor blocked by the task-row lock via
  `FOR UPDATE NOWAIT`), plus the new web test in
  `tests/collection/test_web_sources.py` (flash names the paused task, task
  really inactive) and `tests/api/test_api_permissions.py::test_sources_crud`.
  Verified: 15 passed in one sequential run against exactly the content of
  this commit; `git diff --check`, black/isort on the new files and
  `compileall` passed. Known unrelated failure in the same API suite
  (`test_tasks_crud`: `TenantContextError` from the in-flight P1-T02 admission
  lock) is not part of this change. Migration implications: none.
  Codex review: the executor-reported passing tests above do not cover a real
  stale/concurrent task save. In `app/web/tasks.py`, `_check_task_sources`
  does not reject missing IDs and `_can_activate` accepts a nonempty submitted
  selection even when none of those sources exists. After deletion pauses a
  task and clears its last link, a stale form can commit `is_active=True`
  before `_replace_task_sources` fails inserting the deleted source's FK.
  The already committed task then has no links and falls back to all-active.
  `AgentTaskManager.set_sources` also changes the m2m through
  `_replace_secondary` without taking the task-row lock; the new test's raw
  `FOR UPDATE NOWAIT` proves only that the deletion holds a lock, not that the
  real editor participates in the same protocol. Required before acceptance:
  validate missing sources and make task-definition/source-link saves atomic
  under a lock order shared with deletion; add focused PostgreSQL/ASGI coverage
  of a stale form and actual competing source-link edits, including rollback
  preserving the pause. Static review and `git diff --check` completed;
  Codex did not rerun pytest or reproduce the race at runtime. Review remains
  applicable at current dev `ccce280` (only API validation/tests changed).
  Fix for that review: `app/web/tasks.py` — `_check_task_sources` now refuses
  ids of sources that no longer exist, before `_activate_selected_sources` and
  `_can_activate` can act on them, with a named client message; both create and
  update write the task row and its links in one transaction
  (`_replace_task_sources(..., session=...)`, manager `set_sources(session=...)`),
  so a link refusal rolls the row back instead of leaving a committed
  `is_active=True` without links. `set_sources` in the current tree goes through
  `_mutate_task_sources`, which takes the same admission gate and task-row lock
  as the deletion — the competing editor now waits in the same order (that
  manager change is the in-flight P1-T02 work, not part of this task).
  New coverage: `tests/web/test_web_task_edit.py` — stale form after the source
  was deleted (refused by name; name unchanged, task inactive, no links) and a
  link-only failure with the pre-save check stubbed (renamed task rolls back,
  pause preserved); `tests/collection/test_source_deletion_scope_db.py` — a
  real competing `set_sources` from another session waits for the open deletion
  and lands on the paused task with exactly its own links — it blocks on the
  m2m rows the deletion has already removed (and on the shared admission gate
  first, once the in-flight P1-T02 lock lands). Verification: 10 passed for
  `test_web_task_edit.py` (29 with the selection/collection form suites), 8
  passed for `test_source_deletion_scope_db.py`, 7 passed for
  `test_web_sources.py`.

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
