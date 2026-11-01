# P11-T12 — audit of PR #272 (action-audit persistence)

Candidate: PR [#272](https://github.com/Starck43/social-media-ai/pull/272),
`feat/P11-T12-action-audit-impl` @ `93f2bf15` (+ accepted author fix `40a3d6e7`).
Audit lane: `audit/p11-t12-pr272`, `origin/dev` `cc56c415` merged with the PR
(`5fc82df3`), the author fix cherry-picked (`7f76ee4a`) and corrections below.
Contract baseline: [P11-T12](p11-t12.md). Date: 2026-10-29, opencode.

## What was read

`docs/tasks/p11-t12.md`, `docs/audit/p11-t12.md`, `docs/ACTION_AUDIT.md`,
`docs/API.md`, `docs/MODELS.md`, `AGENTS.md`, `docs/COLLABORATION.md`
(Tasks, State machine, Acceptance, Database, Git and cleanup), `tests/AGENTS.md`,
the PR diff, and these sources in full: migration `0118`,
`app/models/audit/action_audit_event.py`,
`app/models/managers/action_audit_event_manager.py`,
`app/services/action_audit.py`, the `task_launch`/`agent_task_manager` hooks,
`app/api/v1/endpoints/action_audit.py`, `app/jobs/handlers.py` (prune),
`scripts/schema_guards.py`, `scripts/setup_test_db.py`, both new test files.

## Findings and corrections in this lane

1. **Guard parity (correctness, fixed).** `0118` creates
   `guard_action_audit_mutation` plus the `action_audit_events_immutable`
   (UPDATE/DELETE) and `_no_truncate` triggers, but the guard was absent from
   `scripts/schema_guards.py`. A metadata-built test schema therefore accepted
   evidence mutations a migrated one refuses, and
   `tests/tooling/test_p11_t24_schema_guards.py` failed closed. Fixed in
   `e1bd78a6` (module entry + `ONE_OFF_TABLES`).
2. **Replay evidence (acceptance criterion, uncovered).** The card requires that
   a replay not duplicate the event; no test proved it. Added
   `test_replayed_launch_receipt_does_not_duplicate_the_audit_event` (`64611727`),
   which shows the replay branch returns the stored job before the success
   write, leaving exactly one event.
3. **CI lanes (blocking, fixed).** `offline` failed on Black/isort for nine
   changed files and `references` on a stale `docs/generated/api-routes.md`;
   `docs/ACTION_AUDIT.md` also pointed at the contract PR #269 instead of #272.
   Fixed in `7273697e`.
4. **Fixture teardown (regression, fixed).** `action_audit_events.tenant_id` is
   `ON DELETE RESTRICT`, so session-scoped fixtures that deleted their workspace
   after an admission failed with `fk_action_audit_events_tenant`
   (`test_p8_t29_social_launch`, `test_p8_t39_journey_acceptance`,
   `test_p8_t40_composed_admission`, `test_task_run_now`,
   `test_merged_home_acceptance`, `test_api_permissions._drop`). `0a817f19` adds
   `tests/p11_t12_audit_cleanup.py`: it ages the fixture's own rows and expires
   them through `ActionAuditEventManager.delete_expired`, so teardown keeps
   exercising the plan read, the retention marker and the guarded delete. The
   immutability trigger is disabled for that one aging UPDATE (the application
   role cannot set `session_replication_role`) and is never disabled globally.
5. **Web run unaudited by tests (coverage, fixed).** `test_task_run_now` now
   asserts the queued web admission wrote its `succeeded` evidence, and no
   longer pins a create-form flash string that belongs to another suite.
6. **Reported, not changed.** `delete_expired` can return `1` when several rows
   are expired (a planner-visible `LIMIT` flips the in-list subquery to a
   GenericPlan in the Postgres build here). It stays correct across repeated
   passes, but retention needs one call per row in the worst case; the batch
   test asserts the final state, not the count. Worth a follow-up on the
   cleanup job, not a blocker for the contract.
7. **Pre-existing, unrelated.** `tests/jobs/test_p8_t37_analysis_scope.py` (8)
   fails identically on `origin/dev` without this change, and
   `tests/jobs/test_task_run_now.py::test_saving_and_running_queues_the_job_instead_of_blocking`
   fails there too on its flash assertion only (reproduced on a clean
   `origin/dev` build schema). Three failures in
   `tests/infra/test_test_db_bootstrap_safety.py` are the fake `Engine.begin`
   harness on `dev`.

## Checks run (local, exclusive `test_schema`)

- `python -m scripts.ci.checks` (pinned Black 25.1.0 / isort 6.0.1 from
  `.ci/requirements.txt`): syntax 1107 files, migration graph 110 revisions one
  head `0118`, no baseline drift.
- `python -m scripts.ci.run_offline`: **160 passed**.
- Reference lane: `probe.py --guard-check` 7 tripwires, `generate.py --check`
  3 tables, fixture rendering 2 tests, both Node suites, 190-test discovery — all
  PASS.
- Migration `0118` applied to `test_schema` after dropping the stray metadata
  copy: table + function + both triggers (tgtype 27/34), model type
  `actionauditevent` (`audit.action_audit`), VIEW/EXPORT permissions granted to
  owner/admin, `alembic_version` = `0118`.
- Fresh metadata build (`scripts/setup_test_db.py`, 71 guard objects): PASS.
- Focused suites on `test_schema`:
  `test_p11_t12_action_audit`, `test_p11_t12_action_audit_logging`,
  `test_task_pending_occurrence`, `test_task_run_now`,
  `test_p8_t29_social_launch`, `test_p8_t39_journey_acceptance`,
  `test_p8_t40_composed_admission`, `test_api_permissions`,
  `test_p11_t24_schema_guards` — **105 passed, 0 failed, 0 errors**.
- Cleanup: `test_schema.action_audit_events` back to 0 rows, temporary build
  schemas (`p11t12_build`, `p11t12_scoped`, `p11t12_base`) dropped, no
  `catalog-*` rows, nothing written to `public`.

## Verdict

**ACCEPT with findings.** The contract is implemented as accepted: tenant-fenced
append-only events in the admission transaction, separate denied/rolled-back and
unknown paths, bounded tenant-first reads/export over the allowlist, plan-aligned
retention and a reviewable `0118`. Findings 1, 3, 4 and 5 are corrected in the
lane; findings 6 and 7 are recorded for their owners (retention batch behaviour,
pre-existing harness/flash drift). Integration waits for the owner's choice
(push to PR #272 or merge the lane).

## Later integration record

The P11 phase closeout subsequently records this implementation integrated
through PR #272 as code `6ab81b14` in merge `bc41326a`; see the
[P11 phase summary](../phases/p11-structure.md). Q-14 later folded the former
standalone `0118` migration into `baseline_0118` ([baseline audit](q-14.md)).
The source pins, checks and nonblocking findings above remain historical; this
integration record does not imply production deployment.
