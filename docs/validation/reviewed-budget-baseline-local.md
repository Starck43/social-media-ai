# Baseline provisioning — current local validation assignment

PR197, branch `feat/reviewed-budget-baseline`; latest reviewed dev
`edbe0fc02e6ea2d78302129e51febfa0e87464ab` is authoritative. Do not revert owner
changes. Current implementation/check checkpoint:
`2e8a769f3bc609e07e4631e61f61ee97b0798691`; subsequent documentation-only heads
must be verified against the fresh PR head before local execution.
[Evidence and original pins](reviewed-budget-baseline.md).

## Scope — proceed beyond read-only, with an explicit bounded write grant

Read-only is the preflight, not the acceptance outcome. The required finish line
is real PostgreSQL migration/transaction/race evidence. Remote work cannot execute
localhost. Prepare the actual new PG harness and safe normal Alembic launcher now;
then use the explicit owner grant below when forwarded/approved by the owner.
Do not present this proposed grant as an already executed or remotely verified DB
operation. No change to shared pytest's read-only startup is needed or authorized.

Proposed owner authorization for forwarding:

> Authorize bounded PR197 acceptance writes ONLY in
> localhost:5432/social_manager_pr180_migration, schema public, verified executor
> starck. After fresh read-only identity/catalog/writer checks and a verified
> private backup, apply the unchanged access0096 normally ONLY if the target is
> verified0095 and the access preservation/cutover plan is reviewed; then apply
> baseline0097 normally ONLY from a verified access0096 state. Use one revision
> step at a time, with independent readback and retained before/after evidence.
> Prepare and run the new PG197 harness with identified, recorded, non-colliding
> synthetic fixtures. Do not alter prior business/evidence rows, create/drop/reset
> databases/schemas, stamp/replay old revisions, disable guards, weaken tests,
> change grants/seed existing role catalogs, truncate, perform evidence cleanup,
> touch social_manager/other targets, activate clients/runtime/providers or deploy.
> Stop only on target/head/ownership drift, missing/unverified backup, unexpected
> catalog/data/ACL deltas, failures or uncertain outcome; never retry a write
> blindly. If fixture privileges/roles or a disposable guard-test target need an
> additional scope, report that specific requirement rather than escalating it.

Once this exact scope is owner-authorized, do not stop at read-only merely because
pytest startup is read-only and do not repeatedly request the same approval.
Read-only preflight, explicit maintenance and write-enabled tests are separate
steps. Guards/backup/source/target uncertainty still stops execution. Keep Draft
and UNWIRED until the returned real-environment evidence is reviewed.

No executable PG197 harness/launcher has yet been delivered or tested. Existing
`tests/acceptance/test_llm_budget_pg.py` and `scripts/prepare_llm_budget_fixture.py`
are PR180 material, not PR197 tests or permission to replay old preparation.

## Checkout and execution route

From `/Users/admin/Projects/social-media-ai`, first fetch, inspect HEAD/status and
worktree list; fetch is not a checkout update. Preserve unrelated staged/dirty
owner work and evidence. Owner-reported review path is
`/Users/admin/Projects/social-media-ai-pr197-review`: inspect/reuse only if suitable;
never overwrite a dirty review. Verify fresh PR head and dev. Use an isolated task
branch for the bounded harness preparation, not direct dev changes or a second PR.
If unexpected head/ownership changes appear, stop and reconcile.

Read current AGENTS.md, .agent/rules.md, .agent/workflow.md, local skill,
COLLABORATION, board, current baseline contract/evidence and ACCESS_MIGRATION.
Do not read .agent/test-report.md into coordinator context. New local commits go
through OpenCode after a task-scoped staged diff; no direct coordinator commits.

All local pytest follows current R10: OpenCode primary, exact model
`QwenPlan/deepseek-v4-flash-0731`; Kilo secondary only if primary is BLOCKED.
No direct coordinating-shell pytest. Use project venv, attached task file,
collection before changed imports, explicit paths and `--noconftest -o addopts= -q
--tb=short --no-header`. DB checks strictly sequential; `pgrep -fl pytest` precheck
and independent target/writer verification. Runner may not migrate/seed or repair.

Do not repeat the accepted remote56/135, latest targeted10/16, old PR180 acceptance
or the separately accepted29 access cases without a concrete changed risk.

## Read-only target preflight

Selected database: `localhost:5432/social_manager_pr180_migration`.
Historical owner report: schema public, executor starck, sole revision0095,
no llm_budget_baselines. None of these are a fresh verified state.
No remote database access/readback is implied. Do not silently switch to
social_manager, social_manager/test_schema, PR168/179 or old PR180 synthetic target.

Verify actual identity, effective Settings/SQL target, exact schema/revision,
existing rows, model/permission/role metadata, catalog/ACL/ownership/sequences,
enabled receipt/outcome/reservation guards and absence of writers. Keep private
manifests/archives outside Git with restrictive permissions; no secrets in logs.
Preserve all historical manifests, including prior limitations and retained data.
If inventory is missing or ambiguous, stop before writes. A table count is not
catalog/evidence parity. Keep count-only/redacted coordinator output.

## Migration decision — no automatic chain

- At0095: prepare a separately reviewed target-specific access0096 preservation/
  cutover plan first. Access0096 changes roles/permissions and is not baseline
  fixture setup. Do not run upgrade head or any chain automatically.
- At verified access0096: prepare normal0096→0097 only. Baseline0097 adds internal
  provenance; no new catalog Resource, Permission or role grant is intended.
- At0097: do not reapply; prove exact catalog/provenance and whether the target
  is compatible with this source. Unexpected/partial state is STOP.
- If old baseline0096 was applied, preserve its revision/catalog/rows and stop;
  no stamping, numbering repair or migration-definition substitution.
- The shared test_schema is unversioned. Its exceptional access0096 Operations
  run is not a normal Alembic chain, not approval for another operation and not a
  substitute for the selected versioned migration DB. Never stamp it.

Prepare a launcher that suppresses both dotenv mechanisms and verifies the
effective target before DDL. Use normal repository Alembic after reviewing
migrations/env.py registration. Show exact commands, before/after manifests,
private backup verification, allowed expected deltas and evidence-preserving
rollback/stop plan BEFORE requesting/using DDL authorization. Do not disable
triggers or run definitions manually to obtain a pass.

For baseline0097, capture the before-manifest on the reviewed ACCESS0096 state,
then compare after0097. Access changes before that point have their own separate
evidence. Existing resource reconciliation may still run in env.py; review all
model/permission/role/sequence deltas instead of assuming none. Baseline-specific
resource/grant creation is not expected.

## New bounded PG harness / acceptance

Prepare bootstrap-free tests in newly owned PR197-only paths; retain owner code.
Use existing models/managers/session conventions, no ad-hoc sessions/engines.
Collection must not contact a DB; execution must require explicit private approved
target/fixtures and verify identity/catalog before writing. No root conftest,
implicit service bootstrap or unrelated suite. Default pytest validates all mapped
tables read-only: missing baseline0097 is a real precondition, not permission to
create it or weaken validation.

Fixtures need separately approved synthetic identities/ranges and a preservation
plan. Do not create/drop schemas, seed roles, change grants or delete evidence in
teardown. Guard-negative tests must not disable retained-table guards; arrange a
separately approved disposable target or leave the case explicitly deferred.

Required observed cases:
- fresh frozen day + exact original provenance in one acknowledged owned commit;
- identical/conflicting same-payer/day races and separate payer isolation;
- provisioning vs admission payer→day order; frozen day denies dispatch;
- precommit/second-insert rollback, cancellation and real server lock release;
- uncertain commit acknowledgement never returns success; independent readback
  and explicit same-packet replay only; label every synthetic fault as synthetic;
- replay preserves current counters/balances/freeze; legacy/orphan/conflict refusal;
- immutable UPDATE/DELETE/TRUNCATE and initial-snapshot binding/constraints/FK;
- no earlier-day reset, expiry/refund, release, rollover or automatic unfreeze;
- no implicit baseline permissions/grants; no client/HTTP/runtime activation.

Show exact harness SHA/diff, collection outcome, test paths, fixture write scope
and task-file commands. After target-specific write approval and migration
readback, delegate only that test scope via the current primary route. Keep DB
acceptance, normal migration evidence, mock checks and source review separate.
Do not claim real network-loss proof from injected ack loss.

## Return report

Return exact source/test/launcher SHAs and dirty diffs; actual target/schema/
revision/executor; approval boundaries; commands/exit codes; collected/passed/
failed/skipped counts; retained before/after manifests and measured deltas;
scenario-by-scenario real vs synthetic/deferred results. Preserve original PR180
and owner-local evidence attribution. Do not merge/mark whole gate accepted.
GitGuardian finding on the public dev SHA is a separate classification task, not
a reason to obfuscate the pin or disable CI. No green security scan is inferred.
