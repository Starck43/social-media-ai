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
  tests; owner: opencode; status: READY. Confirmed at `a11f1f9`:
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

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
