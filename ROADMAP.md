# Audit execution ledger

This file is an execution checklist populated only for one explicitly
requested audit or long-running Goal. It is not the product backlog; that remains
[docs/design/README.md](docs/design/README.md).

Add task rows only after an audit verifies a gap against current code and
deduplicates findings. Keep this section empty between executions. When an
execution closes, clear completed rows and move still-open product work to the
canonical board.

## Tasks

<!-- Accepted audit tasks are recorded below this line. -->

- [ ] AUD-PR193 — prove source admission on PostgreSQL across concurrent connections, rollback, and ASGI workspace scope; accept: real two-session race admits exactly one job, aborted admission leaves no occupancy, and another workspace cannot run the task; depends: none; owns: `tests/test_task_run_now.py`; owner: Codex; status: BLOCKED — Kilo emitted `sysmon request failed: sysmond service not found`, never returned TEST RUN, and was interrupted; sandbox denied process inspection, so DB/test-process state is uncertain. Resume after read-only confirmation that no child/test process is active and test_schema is safe.

- [x] ACCESS-CATALOG — separate fixed platform/workspace roles and stable domain resources; accept: migration preserves assignments without elevation, registry and enum checks pass; depends: none; owns: types, role/permission models, migration, setup; owner: Codex; status: DONE — local migration postflight and isolated catalog/migration checks accepted; evidence below.
- [x] ACCESS-POLICY — unify admin/API/web and transactional team controls; accept: cross-workspace, operator/member, revocation and last-owner cases; depends: ACCESS-CATALOG; owns: core/API/admin/web/tenant managers; owner: Codex; status: DONE — scoped real PostgreSQL/ASGI acceptance and concurrent last-owner guard passed; evidence below.
- [x] ACCESS-MEMORY — personal memory and explicitly granted team AI; accept: personal transcript/facts/watermarks and approval revocation isolation; depends: ACCESS-POLICY; owns: agent runtime/learning/memory/jobs; owner: Codex; status: DONE — isolated confirmation checks plus real database actor isolation and fresh dispatch revocation passed; evidence below.

### Access validation evidence

At commit `bcba2befad505425684754ad06134947c661b6ff` plus the current access-model
working diff, all checks used the primary OpenCode runner pinned to
`QwenPlan/deepseek-v4-flash-0731`. The original three-file no-DB scope
(`tests/test_access_policy.py`, `tests/test_access_team_policy.py`,
`tests/test_personal_agent_memory_scope.py`) collected and passed 72 tests.

A separate collection-only check of 56 changed test modules collected 863 tests
with no import errors; no tests ran in that check. The final unified 14-file
isolated scope collected 327 tests and passed 327/327 (exit 0; 49 unittest
subtests; four deprecation warnings). Collection and execution used
`.venv/bin/python -m pytest` with `--noconftest -o addopts=`, collection flags
`--collect-only -q --no-header`, and execution flags `-q --tb=short --no-header`.
The exact files and passed counts were: `tests/test_access_policy.py` 60,
`tests/test_access_team_policy.py` 7, `tests/test_personal_agent_memory_scope.py`
6, `tests/test_api_register_privileges.py` 4, `tests/test_identity_model_layout.py`
40, `tests/test_runtime_identity_authorization.py` 18,
`tests/test_runtime_confirmation_authorization.py` 26,
`tests/test_web_perms_active_default.py` 9,
`tests/unit_web/test_workspace_access.py` 66,
`tests/unit_web/test_settings_scopes.py` 37,
`tests/test_identity_permissions_boundary.py` 19,
`tests/test_digest_send_permissions.py` 10,
`tests/test_action_tool_authorization.py` 9, and
`tests/test_tool_dispatch_permissions.py` 16. The access-policy file includes
the migration-0096 duplicate-model permission-map regression against in-memory
SQLite. Full command evidence is in `.agent/test-report.md`.

Final focused rerun of `tests/test_access_policy.py` collected and passed 62/62
through OpenCode after adding the SQLite migration regression. This is a
separate rerun of one file and is not added to the 327-test unified count.

Migration 0096 was applied to the configured local development PostgreSQL
database `social_manager`, schema `public`; Alembic reports `0096 (head)`.
Before the successful attempt, two failed attempts rolled back fully to 0092;
0093–0095 were not stamped or committed separately. A private `public` archive
was validated at `/private/tmp/smai-pre0096-94rkqwmx/pre-0096-public.dump`
(347 archive entries, directory 0700, file 0600). Postflight found 31 resource
rows with no unmapped or retired metadata, the physical empty `oauth_pending`
table intact, platform roles 3, workspace roles 5, and zero orphans across 56
public foreign keys. Business row counts matched the pre-migration snapshot.
Detailed redacted evidence is in `.agent/access-migration-task.md`.

The owner-authorized exceptional update of the existing local
`social_manager/test_schema` applied unchanged revision-0096 operations through
Alembic in one transaction. The schema is unversioned: no Alembic version table
was created, no baseline was stamped, and 0093–0095 were not replayed. A private
custom archive was verified first (399 entries, directory 0700, file 0600).
Postflight preserved the checked test business counts and all five LLM triggers
and three functions; `public` counts were unchanged. Test reference catalogs
now contain three platform roles, five workspace roles and 31 mapped resources.
Ordinary pytest startup validates the shared schema/reference rows read-only.

Real PostgreSQL/ASGI acceptance through the same primary OpenCode model passed
21/21 selected cases at `caec98e` plus the reviewed access-acceptance working
diff (collection and execution exit 0; 33.35 seconds, six deprecation warnings).
The scope was seven `test_api_scope.py` cases, five `test_web_permissions.py`
cases (including three read-only page parameters), two
`test_workspace_member_roles.py` cases, four `test_web_digests_jobs.py` queue
cases and three `test_admin_authorization.py` cases. Every case opted into the
real tenancy guard; trusted bypass was limited to fixture setup/restoration.
The run used `DB_TEST_SCHEMA=test_schema`, `.venv/bin/python -m pytest`,
`-o addopts= -q --tb=short --no-header` and the selected node IDs, with conftest
enabled; collection used `--collect-only` before execution. It covers API
identity/header boundaries, manager creation, viewer denial, platform-admin
membership isolation, scoped queue mutation and explicit operator/admin grants.
The runtime role-manager import fix and stale test assumptions were corrected;
operator default grants and the workspace preset matrix were preserved.

Four additional `test_web_digests_jobs.py` tenancy cases collected and passed
4/4 through OpenCode (both exit 0; 13.21 seconds):
`test_digest_history_does_not_leak_another_workspace`,
`test_jobs_page_does_not_leak_another_workspace`,
`test_platform_admin_as_workspace_viewer_cannot_open_queue` and
`test_read_only_member_cannot_rerun_a_job`. These use the same conftest-enabled
command flags and test target. The protected queue returns 403 to a viewer;
two stale redirect expectations were updated to this explicit denial.

The final PostgreSQL/admin batch collected and passed 4/4 through OpenCode
(both exit 0; 5.73 seconds, six warnings), using the same shared target and
conftest-enabled flags. Exact scope:
`tests/test_access_postgres_acceptance.py` (three cases) and
`tests/test_admin_authorization.py::test_a_regular_user_cannot_enter_the_console`.
The new cases use real managers, tenant/member contexts and database sessions:
two active web owners concurrently demote themselves with exactly one success;
fresh AI grant allows a real `memory_set` dispatch, then revoking AI or disabling
membership blocks the stale identity without a write; two members retain
separate facts, private transcript selections and learning cursors, and foreign
message evidence is rejected without advancing the cursor. Generated tenant
and user rows are cleaned up explicitly. Ordinary platform-user admin entry is
denied by the actual HTTP login/route flow. Total new real acceptance is 29
distinct cases across the three passing batches (21 + 4 + 4), tested at
`caec98e` plus the reviewed access-acceptance diff. The earlier isolated
confirmation tests cover pending-approval authority changes; this batch does
not claim a separate real PostgreSQL pending-confirmation end-to-end run.

`alembic check` still reports broad model/schema drift (including metadata
schema and table/index/FK/default/comment differences); that drift was not
repaired as part of this migration. The accepted access implementation and
bounded acceptance above are complete. Full-suite certification, production
cutover, browser acceptance and remediation of the existing Alembic drift are
separate; none is implied by these results.

## Task format

Each task must have one observable outcome, acceptance evidence, dependencies,
exclusive file ownership and an owner. TODO, IN_PROGRESS and BLOCKED stay
unchecked. Use [x] only for DONE after review and acceptance at the tested SHA.

    - [x] AUD-01 — <outcome>; accept: <evidence>; depends: <IDs or none>;
      owns: <paths>; owner: <agent>; status: TODO

Statuses: TODO, IN_PROGRESS, BLOCKED, DONE, DROPPED. A blocked task stays
unchecked with the blocker and next required input recorded.
