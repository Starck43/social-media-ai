# P6 — Lifecycle and privacy (gate: data lifecycle and privacy contracts hold)

Phase scope and accepted-task history. [Active index](../../ROADMAP.md#p6);
detailed cards are siblings in this directory. [Product direction](../PRODUCT_PLAN.md#foundation-directions).
Historical instructions/permissions below are evidence, not current policy.

Goal: tenant data is isolated and auditable; deletion preserves evidence
required for unresolved effects and accounting.

## Closed tasks

- [x] P6-T03 — add a workspace-admin-only action on the source page to delete
  only that source's raw staged `CollectedItem` rows; accept: explicit
  confirmation, server-side admin and CSRF checks, tenant/source scoped delete,
  refuse while collect/analyze work is pending or running, analytics and
  receipts untouched; focused tests; no live DB cleanup; depends: none;
  owns: `app/web/sources.py`, `app/web/templates/web/source_detail.html`,
  `app/models/managers/collected_item_manager.py` and focused tests;
  owner: codex; status: DONE — accepted by Codex 2026-10-23 at candidate
  commit `57a62d14d4171c59b9ea07931ee6d352a08d8ffa`. Focused OpenCode
  acceptance at that exact commit: 12 passed, 0 failed. The broader combined
  run also exposed five unrelated failures in
  `test_task_editor_json_roundtrips_without_script_breakout` because its
  fixture omits `target_fields`; the focused P6 scope passed on rerun.

- [x] P6-T04 — prevent source deletion from widening an explicitly scoped task
  to all active workspace sources; accept: deleting the final selected source
  atomically pauses or otherwise preserves the explicit task scope; genuinely
  all-active tasks retain their contract; tenant/permission checks and concurrent
  task edits are covered by focused PostgreSQL acceptance; depends: none;
  owns: source deletion manager/service, affected delete callers and focused
  tests; owner: opencode; status: DONE — accepted: codex, 2026-10-22,
  dev SHA `bbb16ba5eb9a48a14c744ad22bebe6db40a07330`. Codex review rejected
  `510f213` on 2026-10-22; the blocker is addressed below. Confirmed at `a11f1f9`:
  source deletion removes m2m links but leaves the task active, while an empty
  link set means all active sources. See
  [social analysis audit](../audit/2026-10-18-social-analysis-web.md).
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
  Codex re-review (2026-10-22), exact SHA
  `92dcb948de24c55ab82dccdba81e792945b5ac48`: the missing-source check and
  transaction boundary address the stale-save finding in the working tree.
  Acceptance is still pending: committed `AgentTaskManager.set_sources`
  does not accept `session`, while the committed web caller now passes it,
  causing `TypeError` on that path in a clean checkout. The session-aware
  mutation, rollback and shared-lock implementation exists only in the dirty
  P1-T02 manager changes. Commit that dependency and attach focused test
  evidence to the resulting exact SHA before DONE. Existing passing counts
  above are executor-reported working-tree evidence, not validation of the
  standalone commit. No pytest rerun during this static re-review.
  Final acceptance: P1-T02 dependency landed in parent `ae18376` commit
  `c9ccf9d4a2328ec2c707b71f45cd7541375daa97`. At integrated dev
  `bbb16ba5eb9a48a14c744ad22bebe6db40a07330`, the app and P6 tests are
  unchanged from that dependency and the P6 implementation. The primary
  OpenCode runner passed the focused root-conftest PostgreSQL/ASGI scope:
  8 `test_source_deletion_scope_db.py` cases and the stale-form and
  link-write-rollback `test_web_task_edit.py` regressions; 10 passed,
  0 failed, 0 skipped. The concurrency case calls real `set_sources` from
  another session and confirms the deletion pause and replacement links both
  survive in the serialized order. `git diff --check` passed. No migration;
  no schema reset, seed or migration. Historical review notes above record the
  earlier blocker and are superseded by this exact-SHA acceptance.

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
