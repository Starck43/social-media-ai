# Archived PR #308 description — historical only

Snapshot of the PR description before the concise handoff. This is provenance, not current instructions or current status. Start with the task card and validation document.

## P12-T14 — single implementation/audit PR

**This is the only task PR to review. Target: `dev`.** Supersedes the slice stack, not another item in it. Keep draft: overall status is IN_PROGRESS and final independent audit is not yet requested. No merge/deployment is authorized. Working branches are retained as historical checkpoints; do not audit/merge them in sequence.

Task card: [P12-T14](https://github.com/Starck43/social-media-ai/blob/dev/docs/tasks/p12-t14.md). Start with [review map and exact source heads](https://github.com/Starck43/social-media-ai/blob/docs/p12-t14-config-security-plan/docs/P12_T14_REVIEW.md).

### Consolidated scope
- VK refresh concurrency fence, encrypted access/refresh storage, migration 0124 and stopped-writer key rotation.
- Encoded OAuth parameters, allowlisted OAuth endpoints, corrected configuration guidance and bounded errors.
- `renewal_unverified`/unreadable-key statuses, reason-aware preflight and personal auth notifications.
- Revocation-preserving owner bootstrap, read-only source/owner inventories and guarded known-unsent unreadable-key recovery.
- Selected Telegram owner checks, removal of global personal-session fallback, reusable same-user personal authorization and explicit per-workspace grants/reuse API, local detach and personal-wide local revoke.

Preserved slices: #314 #317 #321 #322 #323 #324 #325 #326 #328 #330 #331 #332 #333 #334 #335. They are closed without merge as superseded, with original evidence/source branches retained. Protected coordination #311 and other tasks' PRs remain unchanged.

### Integration fixes beyond merely collecting files
- Resolve OAuth guidance conflicts in favor of #322's stronger redaction for both configuration/runtime errors and its provider-verification caveats; retain #323's source-level checks with strengthened redaction assertion.
- Retain #331 `renewal_unverified`, unreadable-key messaging and #334 no-global MTProto behavior.
- One migration head: `0123` -> `0124` -> `p12t14_accounts` -> `p12t14_tg_login`. The latter no longer branches independently from 0123; both secret-only migrations skip unrelated permission-catalog reconciliation.
- Fix key rotation to include the new personal authorization ciphertext. Support only reviewed single schema heads and require matching store layout; future/multiple/unknown generations are refused. Add tests for new-store dry-run/apply, revoked empty placeholder, wrong-key whole-transaction rollback and unsupported layout/head.

### Historical consolidation verification (before the login addition)
Verified implementation checkpoint `e6235c1a121f8c439f1e282501352347b2c8bc43` equals tested local integration commit `d767e8e00b34bee0093344dd64d41c3f7b6e6fb0` at tree `831268f5bd8d3f680315ff5bdcd8375c348e5c9a`. Local ancestry includes all 16 source heads (including original #308). GitHub publication uses file-API snapshot commits; exact tree/provenance mapping, not published merge ancestry, establishes code inclusion. No force push or PR merge.

Dedicated synthetic PostgreSQL only: `localhost:55432/p12t14_synth`, `test_schema`, serialized under the P12-T14 lock. On the 28 task/affected files listed below:
```
python -m pytest <listed files> -k 'not test_migration_round_trip_preserves_legacy_vault' -q -o addopts=''
```
**325 passed, 5 subtests passed, 1 deselected, 8 existing deprecation warnings**, exit 0. All provider/network interactions are mocked. This is a focused set, not the full suite.

- `tests/security/test_p12_t14_access_reasons.py`
- `tests/security/test_p12_t14_account_reuse.py`
- `tests/security/test_p12_t14_bootstrap_inventory.py`
- `tests/security/test_p12_t14_key_rotation.py`
- `tests/security/test_p12_t14_oauth_policy.py`
- `tests/security/test_p12_t14_owner_bootstrap.py`
- `tests/security/test_p12_t14_refresh_claim.py`
- `tests/security/test_p12_t14_refresh_migration.py`
- `tests/security/test_p12_t14_refresh_status_ui.py`
- `tests/security/test_p12_t14_source_inventory.py`
- `tests/security/test_p12_t14_telegram_identity.py`
- `tests/security/test_p12_t14_unreadable_recovery.py`
- `tests/migrations/test_p12_t14_account_grants.py`
- `tests/api/test_p12_t14_account_reuse_http.py`
- `tests/web/test_p12_t14_oauth_guidance.py`
- `tests/unit_web/test_p12_t14_vk_oauth_guidance.py`
- `tests/unit_services/test_connection_status_metadata.py`
- `tests/channels/test_connections.py`
- `tests/collection/test_p12_t16_source_access.py`
- `tests/collection/test_q16_platform_config.py`
- `tests/collection/test_source_owner.py`
- `tests/collection/test_telegram_mtproto.py`
- `tests/security/test_p6_t02_vk_oauth_log_privacy.py`
- `tests/security/test_vk_oauth.py`
- `tests/social/test_p8_t38_vk_connections.py`
- `tests/admin/test_p11_t27_secret_write.py`
- `tests/integrations/test_p12_t08_workspace_connections.py`
- `tests/api/test_q_10_credential_route.py`

The deselected historical P12-T08 broad rewind test fails on the predecessor's model-created/stamped schema (`fk_sources_owner_user_id_users` missing); that same error was separately reproduced on unchanged #334 and was not weakened or counted as passing. New migration/rotation tests do run.

Real Alembic `0123 -> 0124 -> p12t14_accounts -> 0123` succeeded, restoring baseline schema inventory exactly. Test-time schema inventory was unchanged; every predecessor table's before/after row count matched, the new auth/grant tables were empty and all temporary P12-T14 migration schemas were absent. The predecessor was model-created/stamped 0123: this is **not full-history migration proof**. Changed Python AST/Black/isort (57 files), `git diff --check` and one-head check passed. Docs read: README, DESIGN, AGENTS, tests/AGENTS, P12-T14 card/registry, COLLABORATION tests/database, test-runner, API/configuration/connector/collection/account-reuse contracts and relevant source/test files.

### Current login checkpoint — same PR, still IN_PROGRESS

Published implementation `e6f8e44f77af26c3db56964ce51ef475277b7c8e` matches tested local commit
`1446530e1f3210d8a546d5b8aad5776a345bdf41` exactly at tree
`440466928263dcc7f0d0921cc4e6c26e29472302`.

- JWT-only workspace API for phone/code/optional 2FA, status and cancellation;
  no server input(), raw StringSession import or owner/tenant/app-key request fields.
- Ten-minute identity-bound encrypted attempts; no stored codes/passwords; fresh
  actor/membership/rights checks before claim and settlement. One pending login per
  app user across workspaces; three starts/ten minutes, five code/three password
  submissions; strict phases/revisions and durable processing fences before I/O.
- Cross-worker/concurrent replay, late completion after cancellation/expiry/revocation,
  unknown provider outcome, lost DB settlement and cleanup failure are fail-closed.
  A processing step cannot be replayed after restart; cancel/expire/start explicitly.
- Verified get_me identity, personal bundle, first workspace grant and completed
  attempt commit atomically. A duplicate subject never replaces/revives old grants;
  choose the existing account explicitly. No automatic all-account attachment on
  workspace join. An eligible same-user authorization can be reused without another
  code/2FA request via the existing explicit attach operation, with no secret copy.
- New empty-table migration `p12t14_tg_login` follows `p12t14_accounts`; shared-key
  rotation now covers encrypted login attempts too and guards exact schema generation.
- API/configuration/account-reuse/review docs distinguish delivered API from missing
  UI/runtime/recovery, and disclose orphan remote sessions on ambiguous outcomes. At this historical
  checkpoint expiry deletion was opportunistic; the scheduled ciphertext cleanup
  continuation below supersedes that limitation, not metadata retention.

Exact focused command (root conftest, synthetic DB only):
```bash
python -m pytest \
  tests/security/test_p12_t14_telegram_login.py \
  tests/security/test_p12_t14_telegram_login_provider.py \
  tests/api/test_p12_t14_telegram_login_http.py \
  tests/migrations/test_p12_t14_login_attempts_schema.py \
  tests/security/test_p12_t14_account_reuse.py \
  tests/api/test_p12_t14_account_reuse_http.py \
  tests/migrations/test_p12_t14_account_grants.py \
  tests/security/test_p12_t14_key_rotation.py -q -o addopts=''
```
**92 passed, 7 existing warnings, exit 0; no skips/deselections**, fake provider/Telethon
only, no network. This is not a re-run of the historical 325-test consolidation set.
AST/Black/isort of 16 changed Python files, diff and single-head checks passed.

Separate UTF8 synthetic PostgreSQL `localhost:55432/p12t14_login_synth/test_schema`:
model-created predecessor, actual Alembic `p12t14_accounts -> p12t14_tg_login ->
p12t14_accounts` restored the predecessor inventory exactly; upgraded again and ran
final tests against the actual new revision DDL. This is not full-history proof.
Post-test inventory unchanged, all 65 table row counts restored, no login/auth/grant
records or temporary P12-T14 schemas remained; public tables stayed absent.

Development failures fixed before this checkpoint: test basename collision at
collection; HTTP helper/operation keyword collision (two failing HTTP tests).
Initial synthetic SQL_ASCII setup was incompatible with the installed driver;
used a separate UTF8 synthetic DB. A mis-specified inventory --diff invocation was
corrected and the comparison re-run successfully; no working/public target used.
Docs actually consulted: prior session README/DESIGN/AGENTS/tests procedures and
P12 card; this slice API/configuration/account-reuse/review docs, task/registry,
existing auth/reuse/rotation/migration/test contracts. No full suite, live login,
key rotation, real credential read, deployment or PR/dev merge.

### Not done — keep draft / no final audit yet
Bounded phone/code/2FA machine login is implemented (checkpoint below). Reauthorization/recovery, complete operational recovery/metadata retention, CSRF/DOM account/source picker journey, fresh runtime grant resolution and shared-session execution leasing still need implementation. The collector is still legacy user-vault based, so the persistence API does not yet deliver workspace-consented runtime access. API consumer compatibility/full migration-history validation, complete integration and independent audit remain. Q-5/music live-provider gates and rollout approvals stay separate.

No live credentials/session, working/public database, real provider login, full suite, actual key rotation, public migration, deployment or dev merge. All future P12-T14 work goes into this PR, not new slice PRs.


Earlier synchronization from `dev` updated only the task-status card with these results and the new workflow. It does not change implementation or test files; the implementation test checkpoint above is retained rather than claiming a new code test run.

### Remote continuation — partial item 5: scheduled ciphertext expiry

Published in the existing draft #308 as `ec6835f44f18b58d3bfef0b9cf330e2c077ed652`.
Its tree `807f3824c494c546ee2dca235820e60888a243e2` is verified equal to tested
local commit `a1447473f8db96dba4068504da98f9c9ea0a9039`. Fresh dev synchronized:
`bf2449635ea1498b16075062b170c2e343dfa488`; no overlapping code change in this slice.

The worker now starts an independent trusted expiry loop even when the normal
scheduler is disabled. Each transaction fences at most 200 expired pending attempts
with DB time and SKIP LOCKED, advances revision and clears ciphertext without
returning/decrypting secrets or calling a provider. Full batches retry after one
second; otherwise the interval is 60 seconds, with bounded error-code diagnostics
and retry. Request keys/terminal metadata remain for replay protection. This is
best-effort, not a TTL/SLA or physical WAL/backup erase, and it does not log out
or revoke a remote Telegram session. The earlier "no scheduled purge" statement
is historical and superseded for ciphertext expiry only. Metadata retention,
complete operational recovery and rollout checks are not complete.

Remote no-database exception: isolated Python 3.14.10 venv, fake session/settings,
network connection/DNS guard, explicit tests only. Exact command from the owned
checkout (all settings below are synthetic):
```bash
env -i PATH='/data/p12t14-work/stable/bin:/usr/bin:/bin' \
 HOME=/data/p12t14-work PYTHONPATH=/data/p12t14-work/no_network:.:tests/jobs \
 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
 POSTGRES_URL='postgresql://fake:fake@127.0.0.1:1/fake' \
 SECRET_KEY='synthetic-only-secret-not-for-use' DB_SCHEMA=test_schema \
 /data/p12t14-work/stable/bin/python -m pytest -p pytest_asyncio.plugin \
 tests/security/test_p12_t14_login_maintenance_unit.py \
 tests/jobs/test_dispatcher_log_privacy.py tests/jobs/test_returned_job_failures.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
**44 passed, 1 existing SQLAlchemy deprecation warning, exit 0; no skips or
deselections**. Repeated after handoff on the same pinned code with the same
outcome. Black/isort on the three changed Python files and diff check passed.
An initial neighbour-test setup without synthetic POSTGRES_URL/SECRET_KEY had
35 passed/4 failed; corrected fake configuration, then reran the full targeted set.
SQL compilation and fake transaction rollback do not prove PostgreSQL semantics.
No DB opened; no DB records/cleanup, migration, provider call, real credential or
session inspection, full suite, rotation, deployment or merge.

Local OpenCode test-runner handoff: use the published branch/SHA above and a
separate approved synthetic PostgreSQL schema under the normal root conftest,
exclusive DB lock and before/after schema inventory/row cleanup procedure.
Required still-unrun acceptance scenarios: two-worker SKIP LOCKED/locking;
expiry vs late login settlement (no bundle install); rollback/cancellation;
locked-row fairness/starvation; unexpired and terminal row preservation;
request-key replay retention across cleanup; outage recovery. Add/run targeted
DB integration tests for these scenarios; the remote unit file is not a
substitute. Full recovery/metadata-retention policy, runtime grants/leases,
CSRF/DOM UI, reauthorization/no-workspace revoke, API compatibility and complete
migration-history validation remain open. Q-5/music/live rollout remain separate.

Read: AGENTS; README; DESIGN; remote-task; test-runner; tests/AGENTS;
COLLABORATION Tasks/Evidence/Tests/Database; registry/card; account-reuse/review
contracts and relevant dispatcher/login/model/test call sites. Owned continuation:
`/data/p12t14-work/repo`, branch `p12t14-session`; publish only to #308 branch.
P12-T14 stays IN_PROGRESS; #308 stays draft, no READY or final audit request.

### Remote continuation — partial item 1: pinned source-grant inspection

Published in the existing draft #308 as `459d5a8d696a1baeb381ad9a82f864ffc029015b`. Verified publication tree
`70ed1f627f7653e1b32786ae15ad97ced3f153b6` equals tested local commit
`6da0a4e83da7eab5d957db937daded3463a1643e`. Synchronized fresh dev:
`240a236b611412eaedc5d6c2b8954dc4e2cc19ca`; owned implementation paths unchanged.
The prior maintenance checkpoint in the dev card is also preserved in this PR.

Added `telegram_grant_access`: an immutable source/workspace/platform/target/owner/
grant/revision pin and fresh joined metadata inspection. It rejects missing,
wrong-tenant or bypass context; malformed selection; stale source target/owner/
grant/revision; inactive source/platform/workspace/user/web membership; invalid
workspace role; wrong grant tenant/owner; inactive consent/account and stale
consented/account revision. No account discovery, legacy-vault/env fallback,
provider call, decryption or authorization ciphertext projection occurs. Owner
source.view and job-initiator/payer execution rights are separate checks.

**Preparatory only:** the snapshot is not an execution permit or shared-session
lease, cannot prove envelope/provider health and is not wired into the collector
or a public binding write/picker. The collector still uses the old personal vault.
Do not claim runtime grant enforcement is complete. Durable lease, in-lease secret
resolution, request-boundary checks and output/admission fencing remain open.

Remote no-database exception: isolated Python 3.14.10 venv, fake sessions/settings,
explicit network/DNS denial, no root conftest or DB. Exact targeted command:
```bash
env -i PATH='/data/p12t14-work/stable/bin:/usr/bin:/bin' \
 HOME=/data/p12t14-work PYTHONPATH=/data/p12t14-work/no_network:.:tests/jobs \
 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
 POSTGRES_URL='postgresql://fake:fake@127.0.0.1:1/fake' \
 SECRET_KEY='synthetic-only-secret-not-for-use' DB_SCHEMA=test_schema \
 /data/p12t14-work/stable/bin/python -m pytest -p pytest_asyncio.plugin \
 tests/security/test_p12_t14_telegram_grant_access_unit.py \
 tests/security/test_p12_t14_login_maintenance_unit.py \
 tests/jobs/test_dispatcher_log_privacy.py tests/jobs/test_returned_job_failures.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
**125 passed, 1 existing SQLAlchemy deprecation warning, exit 0; no skips or
deselections.** The new unit file separately passed 81 tests before the pinned
combined run. AST/Black/isort passed for all three new Python files (including the
unrun integration test file); diff check passed. Initial commit setup lacked Git
author identity; corrected using per-command author options, not global config.
No tests ran in that failed commit invocation; no test failures in this slice.

Authored, **not executed**, PostgreSQL integration handoff:
```bash
python -m pytest tests/security/test_p12_t14_telegram_grant_access.py \
 tests/security/test_p12_t14_account_reuse.py -q -o addopts=''
```
Local OpenCode test-runner must pin the published SHA, use normal root conftest,
exclusive approved synthetic PostgreSQL/test_schema ownership and before/after
schema inventory plus row cleanup. The new file exercises actual joins, same-owner
foreign-workspace grants, current user/tenant/membership/role/consent/account state,
stale queued source binding/target/revision and post-revoke recheck; it deliberately
proves that metadata inspection does not decrypt/check provider validity. These
are unrun requirements, not PASS. Concurrent lease/revocation/request/settlement
coverage still belongs to the next runtime slice. No migration changed or run;
no DB opened, no records or cleanup, no full suite, real credentials/sessions,
provider API, rotation, deployment, merge or final audit.

Read this slice: remote-task, AGENTS, test-runner, tests/AGENTS; COLLABORATION
Tasks/Evidence/Tests/Database; dev task card/registry; connector-authorization,
source/owner/tenant/access-catalog/collector/connection-status model and service
call sites; relevant existing test fixtures and account-reuse/review contracts.
Owned resume checkout `/data/p12t14-work/repo`, branch `p12t14-runtime-grants`;
previous tested `p12t14-session` remains as an unmerged provenance checkpoint.
Publish only to `docs/p12-t14-config-security-plan` / #308. All six directions
remain open in whole or in part; IN_PROGRESS, draft, no READY or audit request.

### Remote continuation — partial item 4: local personal revoke without a workspace

Published to the existing draft #308 as `26e2703160db1f60db3b80795438672539f71f47`. Verified tree
`9b66e6c59d51d0cc06ee573e5f73bcba9b11499f` equals tested local commit
`c898c7b3fb11c404813550f962a27f0b8bf6d389`; fresh synchronized dev
`192aec0580bbb3b7d4239a4958db414b5f54cf2c`. No intervening owned-path changes.
Earlier dev-card checkpoints are preserved in the implementation branch.

Added account-level `GET /api/v1/connector-accounts/personal` metadata list and
made existing `POST /api/v1/connector-accounts/{id}/revoke` independent of workspace
membership. Both still require an active access Bearer JWT and a fresh locked
active-user check. Exact method/path middleware exceptions never grant tenant or
operator bypass; tenant headers do not select or expand scope. Attach, grant
list/detach and Telegram login remain membership-bound. Strict revision/body
validation and opaque foreign/stale selection errors are preserved. No cookie
or anonymous authentication is accepted.

Owner-only local revoke locks the selected authorization, advances revision on
transition, clears ciphertext and deactivates all its own grants atomically.
It neither selects/decrypts the authorization ciphertext nor requires the key.
Repeated current-revision revoke repairs inconsistent revoked ciphertext/active
grants without another revision bump; stale revision is refused. The old internal
workspace-scoped revoke service retains its fresh membership check and delegates
to the same invalidation helper. Personal list returns only own Telegram/MTProto
identity metadata, including reauth/revoked states; no grant/workspace information
or ciphertext. This affects only the new authorization/grant store. It does not
inspect, migrate or revoke legacy vault credentials or remote Telegram sessions,
and does not grant-protect the still-legacy collector.

Remote no-database exception: isolated Python 3.14.10 venv, fake ORM/session/service
boundaries, real synthetic JWT/API-scope/route code and explicit network/DNS guard.
Exact seven-file targeted command on the pinned SHA (not a full suite):
```bash
env -i PATH='/data/p12t14-work/stable/bin:/usr/bin:/bin' \
 HOME=/data/p12t14-work PYTHONPATH=/data/p12t14-work/no_network:.:tests/jobs \
 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
 POSTGRES_URL='postgresql://fake:fake@127.0.0.1:1/fake' \
 SECRET_KEY='synthetic-only-secret-not-for-use' DB_SCHEMA=test_schema \
 /data/p12t14-work/stable/bin/python -m pytest -p pytest_asyncio.plugin \
 tests/security/test_p12_t14_personal_revoke_unit.py \
 tests/api/test_p12_t14_personal_revoke_unit_http.py \
 tests/api/test_p11_t08_http_policy.py \
 tests/security/test_p12_t14_telegram_grant_access_unit.py \
 tests/security/test_p12_t14_login_maintenance_unit.py \
 tests/jobs/test_dispatcher_log_privacy.py tests/jobs/test_returned_job_failures.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
**204 passed, 7 existing deprecation warnings, exit 0; no skips/deselections**.
Intermediate focused runs passed 49, then 77 tests before the final pinned
selection; no test failure in this slice. AST/Black/isort passed for six changed
Python files, including the unrun integration file; diff check passed.

Authored, **not executed**, local OpenCode/PostgreSQL assembled-app handoff:
```bash
python -m pytest tests/api/test_p12_t14_personal_revoke.py \
 tests/api/test_p12_t14_account_reuse_http.py \
 tests/security/test_p12_t14_account_reuse.py tests/api/test_api_scope.py \
 -q -o addopts=''
```
Pin the published SHA; use normal root conftest, exclusive approved synthetic
PostgreSQL/test_schema ownership, before/after schema inventory and row cleanup.
The new file covers removal of every membership plus suspended workspaces,
missing encryption key, owner-vs-foreign revocation, all-own-grant invalidation,
repeated/stale revisions and actual transaction rollback after an account flush
with injected grant-update error/cancellation. Those are unrun requirements, not
PostgreSQL PASS. Existing account attach/revoke races need the deferred local
integration rerun too. No DB opened/records/cleanup, migration, real credential or
session read, provider call/logout, full suite, actual rotation, deployment,
merge or final independent audit. Generated full route-index synchronization
and complete API/migration compatibility remain item 6 gates, not claimed here.

Docs read/updated: API account/auth contract, api-route-contract, account-reuse
permissions/machine API and review map; existing auth/API middleware, service,
JWT helpers, account-reuse/API-scope/HTTP-policy tests; prior session rules and
card/registry. Resume `/data/p12t14-work/repo`, own branch
`p12t14-personal-revoke`; publish only to `docs/p12-t14-config-security-plan` / #308.
Earlier own unmerged `p12t14-session` and `p12t14-runtime-grants` branches remain
provenance checkpoints. Do not touch foreign checkouts or branches.

Item 4 is still partial: same-subject reauthorization/recovery, revision/reconsent
and account-level CSRF/DOM web UI remain. Items 1/2 full grant runtime/lease,
item 3 settings/source picker, item 5 PostgreSQL/retention/operational recovery
and item 6 API/full migration-history validation remain open. Q-5/music and live
rollout approvals remain separate. P12-T14 stays IN_PROGRESS, #308 stays draft;
no READY, deployment, merge or final-audit request.


### Remote continuation — partial item 6: explicit API response contracts

Published to the existing draft #308 as `d5f7baa134f19babf706d9a1b73a9006c5a955b3`.
Verified publication tree `bbd80bcb4404781ac011924e8d3f7f0719ae95c4` equals tested
local commit `3f9d950a21a0367cf746f071beb9925f74e42faf`. Synchronized registry
and owned docs with fresh dev `c1d6c5fe3e5a56e27923093eb5ec2abdc883393d`;
no intervening owned-path change. Previous checkpoints are preserved verbatim.

All eleven connector-account operations now declare explicit metadata response
models. Existing success keys/200 statuses, nullable IDs and ISO datetime offset
spelling (UTC +00:00, not changed to Z) remain. HTTP serialization drops unlisted
internal fields, including nested list records. A malformed service projection
returns fixed `500 account_response_unavailable` without raw validation input.
This field allowlist does not make arbitrary declared-field text safe or prove
provider health. Legacy credential login is declared deprecated with its actual
fixed unsupported 409 instead of the misleading OpenAPI 200 credential schema;
request/auth behavior and both credential-disable aliases are unchanged.
The generated route index now includes personal listing; CLI tables are unchanged.
Client/state/error guidance and the stale disable-path inventory are corrected.
No real API consumer was migrated and item 6 is not complete.

Remote no-database exception: isolated Python 3.14.10 venv, real synthetic JWT,
API-scope/router and full-application OpenAPI metadata, explicit fake ORM/services,
network/DNS denial plus DB/encryption/provider tripwires. No app lifespan/startup.
Exact pinned test selection (10 files, including two named tooling tests):
```bash
env -i PATH='/data/p12t14-work/stable/bin:/usr/bin:/bin' \
 HOME=/data/p12t14-work PYTHONPATH=/data/p12t14-work/no_network:.:tests/jobs \
 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
 POSTGRES_URL='postgresql://fake:fake@127.0.0.1:1/fake' \
 SECRET_KEY='synthetic-only-secret-not-for-use' DB_SCHEMA=test_schema \
 /data/p12t14-work/stable/bin/python -m pytest -p pytest_asyncio.plugin \
 tests/api/test_p12_t14_connector_contract_unit.py \
 tests/api/test_q_10_credential_route.py \
 tests/api/test_p12_t14_personal_revoke_unit_http.py \
 tests/api/test_p11_t08_http_policy.py \
 tests/security/test_p12_t14_personal_revoke_unit.py \
 tests/security/test_p12_t14_telegram_grant_access_unit.py \
 tests/security/test_p12_t14_login_maintenance_unit.py \
 tests/jobs/test_dispatcher_log_privacy.py tests/jobs/test_returned_job_failures.py \
 tests/tooling/test_p11_t16_navigation.py::test_reference_tables_have_stable_order_and_no_policy_inference \
 tests/tooling/test_p11_t16_navigation.py::test_only_stable_tables_are_generated \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
**282 passed, 8 existing deprecation warnings, exit 0; zero failed/skipped/deselected.**
The new contract file contains 65 passing cases. It checks all operation outputs,
secret-extra filtering, every login state and two datetime offsets, malformed
projection fixed 500, status/error mapping, unexpected request rejection,
no-bearer/cookie-only rejection, actual projection compatibility without ciphertext,
full mounted OpenAPI models/security, and legacy 409/disable-alias declarations.
An initial 42-case run had 41 passed/1 failed because the test constructed
ConnectionDenied with an unsupported argument; corrected the fake construction
without changing production policy. The next four-file run passed 132 cases,
then the final pinned selection above passed. No failure is hidden/deselected.
AST/Black/isort on all five changed Python files and diff/clean-tree checks pass.

Guarded reference checks on the same pinned commit, exit 0:
```bash
env -i PATH='/data/p12t14-work/stable/bin:/usr/bin:/bin' \
 HOME=/data/p12t14-work PYTHON_DOTENV_DISABLED=1 \
 /data/p12t14-work/stable/bin/python -I -B scripts/reference/probe.py --guard-check
/data/p12t14-work/stable/bin/python scripts/reference/generate.py \
 --check --python /data/p12t14-work/stable/bin/python
```
Seven execution tripwires pass; three deterministic reference tables match.
The guarded generator was run before committing; only api-routes.md changed.
No runtime/worker callback, external network, database or provider execution.

Deferred local OpenCode test-runner handoff on the published branch/SHA:
```bash
python -m pytest tests/api/test_p12_t14_telegram_login_http.py \
 tests/api/test_p12_t14_account_reuse_http.py \
 tests/api/test_p12_t14_personal_revoke.py tests/security/test_p12_t14_account_reuse.py \
 tests/api/test_api_scope.py -q -o addopts=''
```
Use normal root conftest, exclusive approved synthetic PostgreSQL/test_schema,
before/after schema inventory and row cleanup. Confirm actual service projections
across all phases/errors, ownership/workspace policy, revoke/attach transactions
and rollback/cancel, not just fake responses. These checks were NOT executed.
Full migration-history upgrade/downgrade and representative consumer migration
need separate local evidence; historical model-created/stamped migration results
are not that proof. No schema/migration was changed or run in this slice.

No DB opened or records created/cleaned; no real credential/session inspection,
provider API/logout, full suite, real key rotation, public migration, deployment,
merge or final independent audit. Item 6 remains partial. Runtime grant enforcement
and durable session lease, CSRF/DOM settings/source picker, same-subject reauth/
recovery/reconsent, expiry PostgreSQL races, metadata retention and operational
recovery remain open. Q-5/music/live rollout approvals stay separate.

Docs actually read/updated: remote-task, test-runner, tests/AGENTS, AGENTS and
previously loaded README/DESIGN/COLLABORATION procedures; fresh task/registry;
API, route compatibility inventory, account-reuse/review and generated references;
actual API entry/auth/middleware/response projections, reference generator/probe,
and selected existing API/tooling tests. Owned resume `/data/p12t14-work/repo`,
branch `p12t14-api-contract`; previous own unmerged branches are retained as
provenance checkpoints. Publish only to #308 / docs/p12-t14-config-security-plan.
IN_PROGRESS, draft, no READY or audit request. Foreign branches/checkouts untouched.

### Remote continuation — partial item 3: personal Telegram metadata/local revoke web page

Published to existing draft #308 as `9b2780cc0dc3d8d9daa14f9163b2a9527cc0b2a2`. Tree `a4a5df9747e678c12a5591d2dd296032545ddecb` equals tested local `f060c3f2c85e88cfd28870a021770b8b7873aef6`. Registry and task card are synchronized with dev `9b9ad4fb30c33bf59744f0a4eb185c20fa393862`; since tested combined base `fb45ccf103f2618e265e25b17fa32fa684c689e8`, dev only changed the unrelated P12-T12 card. PR #327 still overlaps the shared router import/include, not the new routes or middleware; its other files are untouched. P8-T34 acceptance was reported by the owner, while the fresh dev registry still reads READY; this task does not change it.

Browser-session GET `/app/settings/telegram-accounts` lists only own Telegram/MTProto new-store metadata; POST `/{id}/revoke` requires exact bounded URL-encoded fields, explicit confirmation, revision and expiring CSRF bound to session, actor and this page. Exact method/path exceptions skip membership and tenant lookup without bypass; other workspace routes are unchanged. Errors are opaque, unknown outcomes do not imply rollback/success, and responses are no-store/no-referrer. Revoke clears local new-store ciphertext and invalidates its grants; it is not remote Telegram logout and does not change legacy vault or establish provider health. Login, reauthorization/reconsent, grant management, source picker and runtime enforcement remain unfinished.

Remote no-DB lane, synthetic JWT/session, fake ORM/account services, socket/DB/crypto/provider tripwires, no lifespan. Exact focused selection:
```bash
env -i PATH='/data/p12t14-work/stable/bin:/usr/bin:/bin' \
 HOME=/data/p12t14-work PYTHONPATH=/data/p12t14-work/no_network:.:tests/jobs \
 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
 POSTGRES_URL='postgresql://fake:fake@127.0.0.1:1/fake' \
 SECRET_KEY='synthetic-only-secret-not-for-use' DB_SCHEMA=test_schema \
 /data/p12t14-work/stable/bin/python -m pytest -p pytest_asyncio.plugin \
 tests/api/test_p12_t14_connector_contract_unit.py tests/api/test_q_10_credential_route.py \
 tests/api/test_p12_t14_personal_revoke_unit_http.py tests/api/test_p11_t08_http_policy.py \
 tests/security/test_p12_t14_personal_revoke_unit.py tests/security/test_p12_t14_telegram_grant_access_unit.py \
 tests/security/test_p12_t14_login_maintenance_unit.py tests/jobs/test_dispatcher_log_privacy.py \
 tests/jobs/test_returned_job_failures.py \
 tests/tooling/test_p11_t16_navigation.py::test_reference_tables_have_stable_order_and_no_policy_inference \
 tests/tooling/test_p11_t16_navigation.py::test_only_stable_tables_are_generated \
 tests/unit_web/test_p12_t14_personal_accounts_http.py tests/unit_web/test_p12_t14_personal_accounts_template.py \
 tests/unit_web/test_settings_layout.py tests/unit_web/test_personal_billing_account.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
**366 passed, 8 existing warnings, 0 failed/skipped/deselected**, exit 0. Same selection passed on separately locally combined dev tree `8fe45b16c894d337b85a3f3ac4d0569ecaf1b358` (366/8); that is not the published ancestry. Strict templates and offline synthetic Playwright/Chromium: **24 cases passed, 0 failed**, six states × mobile/desktop × light/dark; 32 screenshots individually inspected. CDN assets were stubbed/no-op: local CSS/no-CDN rendering only, not real CDN or app/server/provider acceptance. The DOM suite checks CSRF/form shape, native confirmation, keyboard, escaping, contrast, overflow and navigation. Black/isort/AST 8 changed Python files, Prettier CSS/JS, diff check; guarded reference probe 7 tripwires and 3 deterministic tables pass.

Development transparency: first HTTP test harness run 38 failed/10 passed due to AsyncClient lifecycle, second 32 failed/16 passed due to double router prefix; corrected harness without weakening assertions, next 48 passed. Initial DOM setup failed before cases due to missing fixture filter/packages, corrected locally. Tailwind CDN fetch returned 403; no retry/live-provider contact, DOM intentionally stubs CDN. First expanded combined run 364 passed/1 failed because the existing strict settings fixture lacked `can_connect` for an earlier template change; fixture updated to match actual status default and a positive connect-action regression added, then both pinned selections passed. A merge-diff-wide whitespace check flagged pre-existing trailing blank in foreign P8-T34 card; own-slice diff check passes and foreign file was not changed. A fresh-dev assertion deliberately stopped publication when dev advanced; reviewed the unrelated card and refreshed.

Local OpenCode test-runner handoff: pin published SHA above and normal root conftest, exclusive approved synthetic PostgreSQL/test_schema, before/after schema inventory and row cleanup. Run existing `tests/api/test_p12_t14_personal_revoke.py`, `tests/api/test_p12_t14_account_reuse_http.py`, `tests/security/test_p12_t14_account_reuse.py`, `tests/api/test_api_scope.py` and add assembled browser-session web/DB tests for no-membership list/revoke, foreign/stale ownership, transaction rollback/cancellation and races. Validate real CDN/browser integration and fake-provider onboarding separately. These are NOT run or passed here. Runtime grant/lease, same-subject recovery, expiry PostgreSQL races/retention, consumer/full migration-history checks remain open. No DB, real credential/session, provider call/logout, key rotation, migration, full suite, deployment, PR merge or independent audit. Keep IN_PROGRESS and draft.

### Remote continuation — partial item 3: workspace Telegram grants web controls

Published to the single existing draft #308 at `5867458d825c58e64127fe3bdb7e25e6faa20931`; publication tree `db69ea8606328bc26c7e14788ba2cefd41c7dfb4` equals tested local commit `5b87bbdd4c8b7d1b1c13fce4e525d594b0e57610`. Fresh dev before publication: `bab840f72b7ee6cc197fdc5e81abc002abb26eee`; no changes to this slice's existing paths since the shared base. PR #327 and #311 remain untouched.

New browser-session workspace page `/app/settings/telegram-accounts/workspace` lists own reusable account and own current-workspace grant metadata; confirmed attach and detach forms invoke existing transactional services. Attach requires source create+view; a source-view-only member can detach their own grant. The CSRF identity includes the session, actor, active workspace and surface: switching workspace invalidates the old form. No form-supplied tenant/owner, duplicate/extra fields, JSON or unbounded bodies are accepted. Selected authorization revision is checked by the service. No-store/no-referrer, opaque denials and explicit unknown-outcome messaging apply. Deactivating a grant is local, not Telegram logout; local state is not provider-health proof. The two lists are separate reads, not an atomic snapshot. No source picker or collector/runtime consent enforcement is delivered; legacy collector still uses the old vault.

Remote no-database exception, isolated Python 3.14.10, project-pinned dependencies, synthetic JWT/session and fake account/ORM services; network/DNS/DB/encryption/provider tripwires. On exact tested tree, exit 0, **302 passed, 7 deprecation warnings, 0 failed/skipped/deselected**:
```bash
env -i PATH='/data/p12t14-nov03-owned/venv314/bin:/usr/bin:/bin' \
 HOME=/data/p12t14-nov03-owned PYTHONPATH=/data/p12t14-nov03-owned/no_network:.:tests/jobs \
 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 POSTGRES_URL='postgresql://fake:fake@127.0.0.1:1/fake' \
 SECRET_KEY='synthetic-only-secret-not-for-use' DB_SCHEMA=test_schema \
 /data/p12t14-nov03-owned/venv314/bin/python -m pytest -p pytest_asyncio.plugin \
 tests/unit_web/test_p12_t14_workspace_grants_http.py \
 tests/unit_web/test_p12_t14_personal_accounts_http.py \
 tests/unit_web/test_p12_t14_personal_accounts_template.py \
 tests/unit_web/test_settings_layout.py tests/api/test_p12_t14_connector_contract_unit.py \
 tests/api/test_p12_t14_personal_revoke_unit_http.py \
 tests/security/test_p12_t14_personal_revoke_unit.py \
 tests/security/test_p12_t14_telegram_grant_access_unit.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
New tests exercise session authentication, tenant-switch/actor/session/surface/expiry CSRF, strict input, scope/header injection, viewer controls, opaque and uncertain errors, projection filtering, escaping, and no-workspace behavior with mocked services; they do not validate the actual database service. Black/isort, compile and own diff check pass. Offline strict Jinja and locally styled Chromium screenshots inspected for active, empty, error and viewer at mobile, active desktop, and active light/dark tall mobile; CDN/scripts disabled. This is not full browser/CDN or keyboard/contrast acceptance. Development-only visual renderer initially rejected the SVG namespace URL as a network reference; fixed its local-only URL check. `prettier` executable was unavailable; CSS uses existing style and was visually reviewed, not claimed as Prettier checked. Earlier Python 3.13 non-project run passed 302; the recorded result above is supported 3.14. No failed test run.

Deferred local OpenCode test-runner: pin the published code SHA, use normal root conftest, exclusive separate synthetic PostgreSQL/test_schema, before/after schema inventory and row cleanup. Run `python -m pytest tests/api/test_p12_t14_account_reuse_http.py tests/security/test_p12_t14_account_reuse.py -q -o addopts=''` and add assembled browser-session/real-transaction integration covering membership/role changes, tenant-switch CSRF, foreign/stale grant and authorization, attach-vs-revoke/detach races, rollback/cancellation and unconfirmed outcome. Run fake-provider and full DOM/keyboard/contrast/CDN checks separately; none are claimed as passed here. No DB opened, migration, credential/session inspection, live provider, full suite, key rotation, deployment, merge, READY or final audit.

Read this session: `AGENTS.md`, `README.md`, `docs/DESIGN.md`, `.agents/skills/remote-task/SKILL.md`, `.agents/skills/test-runner/SKILL.md`, `tests/AGENTS.md`, `docs/COLLABORATION.md`, task/registry, `docs/CONNECTOR_ACCOUNT_REUSE.md`, `docs/P12_T14_REVIEW.md`, related middleware/service/schema/templates/tests, and artifact-design skill. Updated account-reuse and review docs on PR branch. Working checkout `/data/p12t14-nov03-owned/repo`, local branch `p12t14-nov03`; preserve as unmerged provenance. All directions remain open in whole or part, status IN_PROGRESS; no independent audit requested.


Card synchronized into PR in `1e495ee25aff5a2ac930cbd827a4ea327c53120f`; fresh dev card commit `ebfa399a4905867fd5d5ae2e65a6d9678f17e569`. Code tree unchanged since tested publication; the read-only merge-tree check reports no content conflict at this checkpoint.

### Remote continuation — partial item 3: synthetic PostgreSQL/browser transactions

Published in the existing draft #308 at `e69793dc96cc8a4ab8970b3d6e030dfd9b068244`;
publication tree `a9dd4d79b7153fdd482c378d1fded4cb1f634948` exactly equals tested
local commit `b7a45dceff5e0d3386aa1863d95f8917ba8cf99c`. Fresh dev before
publication: `53e0f2487f0c7883973c72088ce9467938a5c9d2`. The pre-edit scope
checkpoint was published directly to dev in `f4bce32ba07938858158488bca58a26f1f149ca8`.
Only `tests/web/test_p12_t14_workspace_grants.py` changes executable content;
application behavior is unchanged. No P12-T12 paths or other PRs were edited.

Added 15 parametrized assembled web/DB scenarios using real cookie middleware,
CSRF, ORM services and transactions: explicit idempotent attach/detach; detach
preserving the account and other workspace; workspace-switch invalidation;
foreign account/grant IDs with valid own CSRF; stale revision after personal
revoke; membership/role loss after render; viewer-only withdrawal; rollback on
post-flush errors/cancellation for new/reactivated grants and detach. A controlled
web-attach-first/personal-revoke-second test checks actual PostgreSQL blocking
with `pg_blocking_pids`, releases attach, then proves all grants inactive and
ciphertext cleared after revoke. It does not certify every race ordering.
Synthetic trusted-adapter results only; test tripwires reject provider login and
legacy vault/session helpers. No real provider/session was used or validated.

Owner's current reminder authorizes an owned synthetic schema for needed scenarios,
not full tests. Own PostgreSQL 15.19 sandbox cluster, dedicated loopback port,
database `p12t14_synthetic`, exclusively owned `test_schema`; no shared/local
operator DB or deployment `.env` was read. Sanitized environment, synthetic JWT
signing key, ephemeral test encryption key, memory-only rate limiter, and Python
network/DNS tripwires allowing only the owned DB endpoint. Used normal root
conftest, Python 3.14.10 and project-pinned dependencies, `ADMIN_ENABLED=true`.
No app lifespan/runtime worker started. Bootstrap used supported metadata creation,
canonical reference data and 74 migration guard objects; this is **not** an
Alembic upgrade or full migration-history validation.

On the exact tested/published tree, exit 0: **51 passed, 7 deprecation warnings,
0 failed/skipped/deselected**:

```bash
python -m pytest \
 tests/web/test_p12_t14_workspace_grants.py \
 tests/security/test_p12_t14_account_reuse.py \
 tests/api/test_p12_t14_account_reuse_http.py \
 -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Scoped Black/isort and `git diff --check` passed. Earlier separate existing
reuse/API run: 36 passed. Initial new-file authoring run: 15 failed because the
sandbox harness set `ADMIN_ENABLED=false`, removing the existing static mount;
restored the normal enabled setting (no app-code fix), then 15 passed before the
final combined run. No result from those earlier runs certifies a different tree.

Cleanup after successful and failed test attempts restored all 64 tables' seeded
row counts/digests (511 canonical baseline rows); no scenario users/workspaces,
authorizations or grants remained. Before/after full `schema_inventory.py` layout
matched (tables, sequences, types, functions, triggers, indexes and applicable
version/model-type checks). After final verification, dropped only the owned
`test_schema`; empty inventory matched pre-bootstrap, no other client connections
remained, and the own cluster was stopped. Baseline-seeded metadata is not live data.

Read in this continuation: remote-task/test-runner skills, AGENTS, README,
DESIGN, COLLABORATION test/DB/state boundaries, tests/AGENTS, task/registry,
DOCS_INDEX, relevant account services, middleware/CSRF/main/templates, setup and
schema-inventory scripts, reuse fixtures and existing test call sites.
Retain own clean unmerged provenance checkout `/data/p12t14-nov03-owned/db-slice`,
branch `p12t14-nov03-db-slice`, at tested local SHA above; the earlier own checkout
is unchanged. No foreign checkout or DB was used.

Still unrun: all other race permutations (including attach/detach), fake-provider
onboarding, browser DOM/keyboard/contrast/CDN checks, consumer/full migration
history, expiry/retention and operational recovery. Source picker, runtime grant
lease/enforcement and same-subject recovery/reconsent remain unfinished. No full
suite, public migration, key rotation, credentials/session inspection, real
Telegram/VK/provider API, merge/deployment, READY or final independent audit.
Keep the task IN_PROGRESS and #308 draft.

### Remote continuation — partial item 1: shared-account PostgreSQL lock foundation

Implemented in #308 at `4eeb7084c2b9051505fd23ee15da57840a1ea09f`, followed by
own-database isolation of the backend-loss fixture in
`96b666e6a663b8a569a5d176b752154c1deca855`. Final published tree
`df5fe1e7b8dffeb2938cd6226ad1cde5656c090f` exactly equals tested local
`9ff40a98efec3e9409ea705532b7eb3d395a018b`. The initial implementation tree
also matched its tested local commit `cb260c574052d8667e67ea5ce2c3e3c82a841b6f`.
Fresh dev before both publications: `2077bb2e2f9fc33dc8337535983eccf22f944334`,
which contains the pre-edit scope checkpoint. Seven owned service/test/doc paths;
no foreign PR/checkout or P12-T12 paths changed.

`telegram_account_lock.locked_source_grant` uses fail-fast PostgreSQL transaction
advisory exclusion in separate namespace `721215`, keyed by personal authorization
ID across workspace grants. It uses an owned READ COMMITTED, READ ONLY session;
pre/post-lock metadata checks must agree. Handle inspection reloads the entire
source/owner/membership/consent/revision pin on that same connection, rejects
cross-task/post-exit/changed-transaction access, and invalidates after a failed
recheck. No reconnection/reacquisition/replay through an invalid handle. Normal
exit, error and cancellation release the transaction lock. Independent account
rows are not unnecessarily serialized. Revoke/detach do not wait for this advisory
lock; the next metadata check detects their changes.

Refactored the existing inspector to accept its owned session internally and
refresh SQLAlchemy identities, preserving early context denial before sessions.
Synthetic integration exposed an old name-based platform assumption: the canonical
seed may use a localized label. The inspector/fixture now use the unique typed
`PlatformType.TELEGRAM`, not a display name; tests explicitly accept a renamed
Telegram label and deny a VK source even with a Telegram grant. No right or
cross-owner/tenant check was removed.

**Foundation only:** no decryption/ciphertext projection, provider or legacy
session access, collector wiring, initiator/payer authorization, automatic TTL/
renewal/stale-worker reclaim, or request/output-admission fence. This is not a
complete durable/renewable execution lease. A metadata result can become stale
immediately afterward. A lost DB connection does not prove an already-sent remote
operation did not happen. Runtime still uses the legacy vault; this slice must
not be deployed as completed workspace-consented collection. No schema migration.

On the final exact tested/published tree, two focused routes, exit 0 each:
**110 passed + 59 passed = 169 passed**, 1 existing SQLAlchemy deprecation warning
per route, 0 failed/skipped/deselected. Python 3.14.10, project-pinned dependencies.

DB-exempt isolated environment with synthetic required settings, network/DNS
tripwires and no root conftest:
```bash
python -m pytest \
 tests/security/test_p12_t14_telegram_account_lock_unit.py \
 tests/security/test_p12_t14_telegram_grant_access_unit.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Normal root conftest, own synthetic PostgreSQL 15.19 with fake authorization data:
```bash
python -m pytest \
 tests/security/test_p12_t14_telegram_grant_access.py \
 tests/security/test_p12_t14_account_reuse.py \
 -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

PostgreSQL evidence includes actual shared-account exclusion across workspaces
and an independent Python process, distinct-account concurrency, READ COMMITTED/
READ ONLY settings, post-discovery changes, identity-map freshness, membership/
revoke rechecks, error/cancellation including before yield, connection termination
and subsequent independent acquisition. The backend-loss fixture constrains its
lookup to its own database. Tests select only synthetic metadata and no provider
operation. DB URL/session signing settings are sanitized; the child worker does
not inherit deployment secrets or a `.env`. Scoped AST/Black/isort (5 Python
paths) and diff checks passed.

Earlier development checks: 30 fixture setup errors from selecting the localized
platform by name; corrected fixture and the relevant inspector contract as above.
Next run: 31 passed, 1 observer-counter assertion failure; moved the test counter
before the awaited operation (no application change for that correction). Then
33 integration scenarios passed. The final two routes were rerun after the extra
own-database backend-loss guard; earlier results do not certify another tree.

DB target: newly owned loopback-only sandbox cluster/database
`p12t14_lock_synthetic`, exclusive `test_schema`, never an operator/shared DB.
Supported metadata bootstrap, canonical synthetic reference rows and 74 guard
objects; **not** Alembic upgrade/full-history evidence. After failed and successful
runs, schema inventory and all 64 tables' counts/digests restored the 511-row
canonical baseline. Final checks found no account advisory locks or other client
connections. Dropped only the owned schema; empty inventory matched pre-bootstrap
and the own cluster was stopped. No scenario records or test backend survived.

Read/refreshed: remote-task skill, current task/registry and #308/open-PR state,
account-reuse/review contracts, relevant source/platform/authorization model and
manager/CSRF/ownership/service call sites and task-specific test fixtures. Reused
unchanged session AGENTS/README/DESIGN, tests/AGENTS and COLLABORATION test/DB rules.
Retain clean unmerged own checkout `/data/p12t14-nov03-owned/lease-slice`, branch
`p12t14-nov03-lease`, at the final tested local SHA above; earlier two own provenance
checkouts remain unchanged. Own transient DB/scripts/output removed after durable
checkpoint publication.

Remaining: in-lock secret/envelope resolution, request-boundary revocation and
output/admission fencing before collector activation; explicit source binding/
picker; same-subject reauthorization/reconsent, expiry/retention/operational
recovery, full consumer/migration-history and full-suite checks. No full suite,
real credentials/sessions, real Telegram/VK/provider API, public migration, key
rotation, merge/deployment, READY or independent final audit. Keep IN_PROGRESS
and #308 draft.


### Latest partial item 1 — in-lock envelope resolution


Owner reaffirmed hardest-to-simplest execution. This checkpoint addresses only
in-lock envelope resolution; runtime request/revocation/output boundaries remain
next, before source-picker and other simpler completion slices. Claim was written
directly on dev at `979adf40223b74648d1dfabfceb8f684ac0e0db4` (pre-claim dev
`357a21608fad0c2eb4fa1e5435003231bdb20112`). Registry owner/status are unchanged.

`guard._setup_material()` is private and not wired to the collector. The joined
read uses all existing source/owner-role/grant/revision predicates and explicitly
pins Authorization.id to the held account lock BEFORE selecting ciphertext. A
rebound grant cannot decrypt a new account under the old account lock. A shared
decoder validates Telegram/MTProto active state, exact envelope/material shape and
identity types/values. Reuse/attach still returns metadata only. A fresh complete
metadata recheck after decryption must pass before yielding setup material.
Task/transaction/tenant/scope ownership is enforced locally; retained values clear
on scope exit, invalidation and cancellation. Repr/copy/pickle/dataclass/vars
surfaces do not expose material. Already-read Python strings cannot be recalled
or securely zeroized. Property access is NOT a fresh database authorization or
provider I/O permit. All failures remain opaque, cancellation propagates and no
fallback/replay is introduced. No public API, source binding write, schema or
migration change; legacy collection remains unchanged.

Final tested local SHA: `432805b2608d23429dad4fdc8a6874506d2a5939`, tree
`f506638d708eff1e0e07bc0ae73c820960f61d85`. Solution published to existing #308
branch in `3f1a7b7f56be2f4b99ac18bd8611009acc176970`. Fetched published paths
match the tested tree exactly apart from this separately synchronized task card;
local implementation checkpoints are `48cc258a8ba644495e0ddf375df251d14a77f8bd`
and `432805b2608d23429dad4fdc8a6874506d2a5939`. No new PR, merge or deployment.

Final focused routes on that exact implementation, Python 3.14.10, exit 0 each:
**182 passed + 77 passed = 259 passed**, 0 failed/skipped/deselected; one existing
SQLAlchemy declarative_base deprecation warning in each route.

Isolated DB-exempt route, sterile environment, synthetic required settings,
network/DNS tripwires, no root conftest:
```bash
python -m pytest \
 tests/security/test_p12_t14_telegram_account_lock_unit.py \
 tests/security/test_p12_t14_telegram_grant_access_unit.py \
 tests/security/test_p12_t14_telegram_envelope_unit.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
Normal root conftest, exclusive newly owned PostgreSQL 15.19 synthetic database:
```bash
python -m pytest \
 tests/security/test_p12_t14_telegram_envelope.py \
 tests/security/test_p12_t14_telegram_grant_access.py \
 tests/security/test_p12_t14_account_reuse.py \
 -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Coverage includes real synthetic Fernet encryption/decryption, all identity pins
and malformed material/key paths, metadata-only non-decryption, task/context/
transaction expiry, copying/serialization refusal, post-decryption rechecks,
retained-value cleanup, cancellation and no replay. PostgreSQL adds actual
same-lock setup, fresh identity-map ciphertext, detach/member/source/revoke/
revision changes both before resolution and after decryption, corrupt/swapped
identity/wrong-key rejection, grant-to-new-account rebinding BEFORE ciphertext
selection and cancellation/reacquisition. Existing cross-workspace/process lock
and account-reuse cases remain in the route. No provider operation was attempted.
Earlier development checkpoint: 181 unit + 76 PostgreSQL passed; executor review
then identified the account-rebinding gap, added the pre-selection SQL account pin
and two regressions, and reran both final routes above. No test failures were
suppressed or assertions weakened. This executor review is NOT independent audit.
Scoped AST, Black/isort (6 Python paths) and git diff checks passed.

DB target was newly owned loopback-only cluster/database
`p12t14_envelope_synthetic`, exclusive `test_schema`; never an operator/shared DB.
Sterile env and a socket/DNS guard allowed only its loopback port, never providers.
Supported metadata bootstrap plus 74 migration-derived guard objects was used;
this is NOT Alembic upgrade/downgrade or full migration-history evidence. Before
and after both PostgreSQL runs the schema inventory and all 64 table counts/
digests matched the 511-row canonical baseline. Final check found no account locks
or other database clients. Dropped only the owned synthetic schema; its empty
inventory matched pre-bootstrap, then stopped the owned cluster. No scenario
records/backend remained.

Read/refreshed: session AGENTS/README/DESIGN, tests/AGENTS, remote-task/test-runner
skills, COLLABORATION test/DB rules, task/registry and #308/open-PR state,
CONNECTOR_ACCOUNT_REUSE and P12_T14_REVIEW, relevant authorization/crypto/lock/
grant/model and setup/inventory/test-fixture call sites. No other agent or PR
was modified. Preserve own unmerged checkout `/data/p12t14-continuation/repo`,
branch `p12t14-runtime-continuation`; older provenance worktrees remain untouched.

Remaining item 1: bounded runtime ownership, initiator/payer authorization,
request-boundary revocation, output/admission fencing and collector integration.
Then source binding/picker, same-subject recovery/reconsent, retention/operational
recovery and PostgreSQL expiry races, API consumers/full migration history, and
fake-provider/browser end-to-end checks. Full suite and genuinely independent
audit stay at the END as directed. No real credentials/sessions or Telegram/VK/
provider calls, public migrations, key rotation, merge/deployment or READY.
Keep IN_PROGRESS and #308 draft.

Pre-evidence synchronization: fresh dev `f5d85ce6d365f4f235e304eb5bcc737798181992`; its unrelated P12-T12 changes are preserved and are not claimed as tested by this slice. P12-T14 card blob and registry ownership remained unchanged.


### Latest partial item 1 — transactional consent admission gate


### Remote continuation — partial item 1: transactional consent admission evidence

Second hard-runtime checkpoint, not collector activation. Scope was claimed
on dev `d662bb126fedae4dd973533800680ed66ef10c4b`; registry ownership and status
remain unchanged. `guard._admission_transaction()` opens a separate short READ
COMMITTED, READ WRITE transaction while retaining the original account guard.
It locks exact account/grant IDs FOR SHARE, source FOR UPDATE, then the joined
platform/tenant/owner/membership/role rows FOR SHARE. All pin and state predicates
are fresh after lock waits. No ciphertext is selected or decrypted. The source
write lock supports future same-transaction receipt/cursor writes without a
lock upgrade. It preserves account -> grant -> source order, compatible with
existing attach/detach/revoke. Statement and lock timeouts are five seconds PER
SQL statement, not a total runtime TTL.

Only writes in that yielded transaction are protected through commit. A consent
withdrawal either commits first and rejects admission, or waits for the receipt
commit. Transaction-local source retargeting fails the final write-session check;
the original guard is also freshly checked before yield and before commit. Any
failure/cancellation invalidates the guard. A commit failure remains opaque and
uncertain: never infer rollback or replay automatically. No provider/model/sender
I/O belongs inside this short write scope. This is a database CONSENT gate, not
a complete runtime/output fence: initiator/payer/job authorization, durable lease
epoch/stale-worker protection, request boundaries and actual collector/staging
integration still remain REQUIRED before activation. The legacy path is unchanged.
No API, schema, migration or real provider operation changed.

Final tested local SHA `fa64abef246e389a1c70c37d1fbac090d33b368e`, tree
`ab4876e6ac90e75cc537c9de49064a22248e7524`. Existing #308 solution commit
`6980063662ceb614eff280e5032be7243a45dd5d`; fetched published implementation
matches that tested tree exactly except the separately synchronized task card.
Local implementation commits: `13bcaf1d` and `fa64abef`. Same PR/branch, no merge.

Final focused checks on that exact implementation: **204 unit + 101 PostgreSQL =
305 passed**, exit 0 each, 0 failed/skipped/deselected, one existing SQLAlchemy
declarative_base deprecation warning per route. Python 3.14.10. Exact commands:

Isolated DB-exempt route, synthetic settings, sterile environment, network/DNS
tripwires and no root conftest:
```bash
python -m pytest \
 tests/security/test_p12_t14_telegram_account_lock_unit.py \
 tests/security/test_p12_t14_telegram_grant_access_unit.py \
 tests/security/test_p12_t14_telegram_envelope_unit.py \
 --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```
Normal root conftest on an exclusively owned PostgreSQL 15.19 synthetic database:
```bash
python -m pytest \
 tests/security/test_p12_t14_telegram_envelope.py \
 tests/security/test_p12_t14_telegram_grant_access.py \
 tests/security/test_p12_t14_account_reuse.py \
 -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Unit checks cover lock order/non-secret SQL, separate transaction ownership,
pre/post checks on both connections, nesting/cross-task denial, callback rollback,
cancellation and opaque uncertain commit/no retry. Actual PostgreSQL checks prove
conflicting NOWAIT row-lock acquisition fails for ALL EIGHT mutable consent rows
(account/grant/source/platform/tenant/user/member/role) during admission and
succeeds after commit; same-transaction synthetic source receipts persist only on
success. Real service detach/revoke waits are observed through pg_blocking_pids,
then complete after receipt commit and deny further admission. The reverse race
commits revoke/detach/member/source changes while the gate waits, and admission
rejects the fresh state instead of an old snapshot. Actual lock timeout, callback
failure/cancellation, transaction-local retargeting and loss of the original
account-lock DB backend before commit roll back synthetic receipt and invalidate
without replay. Backend termination is constrained to the fixture's own database.
Earlier checkpoint had 204 unit and 100 PostgreSQL passing; added the original-
connection-loss regression and reran both final routes. No test failure was hidden
or assertion weakened. Scoped AST/Black/isort (5 Python paths) and diff checks PASS.

Newly owned loopback-only cluster/database `p12t14_admission_synthetic`, exclusive
`test_schema`, not a shared/operator DB. Sterile child env and socket/DNS guard
allow only its PostgreSQL loopback port. Supported metadata/bootstrap and 74
migration-derived guard objects; NOT Alembic full-history evidence. Before/after
both PostgreSQL runs, schema inventory and all 64 table counts/digests matched
511 canonical rows. Final account-lock and other-client checks were empty; dropped
only the owned schema, matched its pre-bootstrap empty inventory and stopped the
owned cluster. No scenario record or test backend remained.

Read/refreshed in addition to previous session rules: current staging/collector
and trusted actor/payer handlers, VK task-scope precedent, workspace connection
_authorize and connector attach/detach/revoke lock order, current task/registry,
source/guard/grant/tests and account-reuse/review contracts. Tests authored and
executed by the executor; this is NOT independent audit. Preserve own unmerged
checkout `/data/p12t14-continuation/repo`, `p12t14-runtime-continuation`; older
provenance checkouts/foreign work remain untouched. Full suite and independent
audit stay at the END. Remaining item 1 and later picker/reconsent/retention/
consumer/migration/E2E gates are not waived. No real credentials/sessions, live
Telegram/VK/provider calls, public migration/key rotation, merge/deploy or READY.
Keep IN_PROGRESS and #308 draft.

Pre-evidence synchronized dev: `d662bb126fedae4dd973533800680ed66ef10c4b`; unrelated dev work preserved.


### Latest code-first checkpoint — point 1 runtime, untested

Code `dcb388dac266158a1399ff7008021c183b510bed` adds a monotonic per-account
execution epoch, persisted job/source/payload and initiator/scheduled-payer pins,
a bounded private history-only Telethon send gate and generation/job-aware output
transaction. Migration is authored, not applied; collector/legacy path is NOT
activated. Protocol transport retransmission is not application exactly-once.
Fresh dev before publish: `68b4d9bbc81d20701b9b4e13b4e5479d4b74ce33`.

Owner changed workflow to code for ALL remaining points first, then all new
test authoring/execution together, then independent audit. No new tests ran here;
Black/isort + AST/diff checks only. Previous 305-test evidence applies ONLY to
the earlier admission tree, not this checkpoint. Approximate whole-task code
implementation: 65%, NOT tested readiness. Remain Draft/IN_PROGRESS; no merge or
deployment. Next claimed code: guarded staging transaction reuse and trusted
claim accessor, without collector activation. Task evidence/claim recorded on
dev in `bab4b9e26ce5c889fc17b9847a5c12b1046c188e`.


### Guarded staging code checkpoint (untested)

`9b0247f3dee7720380aee6fba28554889a8450d8` splits the owning wrapper from the
transaction-owned writer and adds one-attempt runtime-batch staging, with job ID
derived from acquisition and acknowledgement only after fenced commit. Trusted
claim accessor added. Black/isort + two-file AST + diff only; NO new tests run,
no collector activation, no migration apply. Whole-task code estimate remains
~65%; not readiness. Next claimed scope: immutable enqueue bindings and a default-
OFF guarded collection path/selection ledger; final tests and audit unchanged.


### Immutable queue pins + default-OFF collector code (untested)

`875b550ef3d8d273aa006919b9d67759eb34745e` snapshots account selection at
trusted enqueue and rejects missing/changed pins at execution. Default-false
`TELEGRAM_ACCOUNT_RUNTIME_ENABLED` gates the new collection route; old personal
raw-client/vault pull is explicitly rejected (unused legacy pull bodies removed).
Push/sending remain independent. Raw/cursor/selection-ledger writes share the
runtime transaction; account guard exits before AI. History ordering corrected
to oldest-unread-first, rehearsal text/hash previews omitted. No activation.
AST of 11 Python files, targeted formatting/diff hygiene only, NO new tests or
provider/DB/migration execution. All runtime/cursor/consumer/regression evidence
remains in final tests then independent audit. Whole-task code estimate ~70%, not
acceptance. Next: permission-scoped explicit source picker API/web UI. Keep Draft.

