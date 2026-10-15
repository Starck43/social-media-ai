# Reviewed day-baseline provisioning — sandbox evidence, UNWIRED

**CURRENT UPDATE:** access-model dev `eb6cad6c56e78768b6482eba312082b849d24712`
is authoritative. Baseline definition is now0097 after access0096. Prior0096
baseline instructions and PG packets below are historical and MUST NOT be run.
See the final compatibility section; original SHA/count attribution is retained.

[Draft PR #197](https://github.com/Starck43/social-media-ai/pull/197).
[Reviewed packet/fence and unapplied migration contract](../design/reviewed-budget-baseline.md).
Implementation and tests: `b3cdc0fb2bded7a4ed67a235cf1823afffd2584d`.
Dev base: `9c7bf4d146d8086d279ee9d1f80a950af9cdff43`.
Later documentation-only successors do not relabel this tested implementation.

## Executed remote isolated checks

Clean sandbox checkout at the published SHA; separate `/data/baseline-venv`,
not the owner's project .venv. Actual source loaded with module-local explicit
import doubles; no app startup, real sessions, root conftest, .env, credentials,
DB/provider/runtime/HTTP calls. Alembic operation capture uses a double, not a
migration runner. Dependencies installed within repository manifest ranges.

Python 3.13.14; pytest9.1.1; pytest-asyncio1.4.0; SQLAlchemy2.1.4; Alembic1.20.0.
Changed-file formatting: Black26.10.1, isort9.0.2. No unrelated formatting.

```sh
/data/baseline-venv/bin/python -m pytest tests/test_reviewed_budget_baselines.py --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exit0: **36 passed; 122 subtests passed; failed0; skipped0; warnings0** (0.36s).
An earlier local sandbox preparation at `45301070470db462fe8bbd1cef52ba4435c953a2`
passed the same36 tests/122 subtests with four configuration/collection warnings;
the published successor adds strict fence-version type checking, renames the
non-test ORM base and installs manifest-compatible pytest-asyncio. The final
run above is the authoritative published pin, not a relabel of the earlier run.
No accepted PR180 checks were rerun.

Coverage: complete explicit packet/no zero defaults; exact USD limits; mandatory
initial freeze; payer/UTC/counters/review/digest/fence syntax; owned two-insert
mock atomicity; payer→day lock order and scoped SQL; named identity collisions;
exact replay after changed live balances/counters/freeze; conflicting packets;
legacy/manual day and orphan provenance refusal; unrelated integrity rollback;
synthetic pre/post-commit acknowledgement failure, replay commit failure,
rollback error and cancellation; required enabled guard probe; model/definition
column/check/FK/unique parity; schema-qualified immutable and initial-binding
DDL; refused destructive downgrade; no client/HTTP wiring or mutable-day edits.
These are **mock/source/DDL-capture checks**, not real session/PG proofs.
Synthetic acknowledgement loss is not real network loss.

Static review: `git diff --check` exit0; `reserve_receipt` AST unchanged vs base.
`budget_admission.py`, migrations0093–0095 and the original
`llm-budget-pg-runner.md` evidence are byte-unchanged. Published tree was compared
to the sandbox proposal before checkout/testing. No independent reviewer approval
is inferred from this source review.

## Still required — not executed or authorized by this package

Keep Draft pending separately approved real PostgreSQL acceptance through local
sequential Kilo R10: same-payer two-session provisioning/replay/conflict races,
provisioning-vs-admission lock ordering, distinct payer isolation, actual guard/
constraint/catalog parity, second-insert rollback, owned session and commit/readback
uncertainty, normal0095→0096 rehearsal on an explicitly approved target with
protected evidence/catalog manifests. No original social_manager migration,
create_all/reset/drop/stamp/bootstrap, runtime/provider/deploy or cleanup.
Environment-level model/permission registration effects during normal Alembic
execution also require review; operation capture does not exercise migrations/env.py.

No unfreeze/activation, baseline inference/import, reviewed-bound production,
receipt/outcome reduction, settlement/release, rollover/refund or full accounting
matrix is delivered. All new days are frozen. Syntax/digest validation is neither
operator authorization nor verified external fencing. Worker/payer-wide in-flight
coverage and prior liabilities must be inspected in the retained evidence before
submitting the trusted preparatory packet or considering a later activation.

## Preserve historical acceptance

[Original evidence](llm-budget-pg-runner.md) stays at its existing pins and caveats:
remote `d10bde4c3e63a5308e5ae61b8f46b87e63cf3c71` (57 no-DB PASS; one isolated PG
node /14 scenarios PASS; retained11 receipts/10 reservations/18 days; sequence
advancement19/15/18), and owner-reported normal0094→0095 at
`f4c14c4da0fc212591ca0f88c35db3f8a20d87d3` on the approved legacy clone only.
Keep ck_tenants_plan variance, reconstructed before0093, measured before0095,
private evidence not remotely inspected, PR137 and old31/6 caveats. This new
package neither reruns them nor certifies a production/universal baseline.
PR180 remains merged, closed and UNWIRED; PR194 closeout is not reopened.


## Source-review follow-up — completed; real PG remains deferred

Fresh dev is still `9c7bf4d146d8086d279ee9d1f80a950af9cdff43`.
Follow-up test SHA: `5fa6cfcfd9a2095d326aa979f2108d355a20291d`.
Review performed by the implementing remote agent, not an independent reviewer
or owner approval. No source-level blocker was found **within the frozen,
trusted-internal, unwired preparatory slice**. This is not a production verdict.

Reviewed: exact packet fields/Decimal/time/counters, payer isolation and active
payer checks, payer→day lock order, no ambient session, one-time paired insert,
legacy/orphan/conflict refusal, immutable-snapshot replay vs mutable current day,
commit acknowledgement boundary, enabled guard probe, schema-qualified definition/
model parity, RESTRICT retention, normal migration environment registration,
initial freeze and old-worker/in-flight attestation limitations, and unwired calls.

One coverage gap was identified and closed: the earlier transaction double did
not execute the actual BaseManager owned-session helper. A new test extracts and
compiles **that helper's exact AST** with only its session factory replaced,
then exercises the real provisioning method through fake sessions. No BaseManager
DB import graph is executed. The source helper itself is unchanged.

```sh
/data/baseline-venv/bin/python -m pytest tests/test_reviewed_budget_baseline_owned_session.py --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

At clean published SHA `5fa6cfcfd9a2095d326aa979f2108d355a20291d`: exit0,
**11 passed; failed0; skipped0; warnings0** (0.34s), same separate venv/versions.
Checks: factory ownership/commit/resource exit; no success before delayed commit
ack; second-insert rollback; pre/post-commit failure; exact replay after synthetic
lost acknowledgement; replay commit error; rollback failure; cancellation without
commit; rejection of a caller session; real Alembic offline Operations rendering
and nondefault quoted schema across table/FK/functions/triggers.

Offline rendering uses MigrationContext as_sql + StringIO, no DBAPI/engine,
settings/env.py, version-table writes or online migration. It proves emitted SQL,
not PostgreSQL parsing/execution, catalogs or applied trigger behavior. Fake
session close after cancellation does not prove actual server rollback/lock release.
Synthetic acknowledgement loss remains distinct from real network loss.

Original36 checks/122 subtests stay attributed to b3cdc0f and were **not rerun**.
The follow-up imports their harness, not their test collection. Original code,
definition and harness were byte-compared and unchanged. There was no combined
47-test run, full-suite claim or relabel of old evidence. Diff check passed.
Real PG acceptance and operator-authorized activation remain the open blockers.

## Prepared PG acceptance packet — NOT EXECUTED

This is a review/approval packet, not a second backlog or an executable DB runner.
Target database/schema, executor identity, allowed DDL and fixture write scope,
existing revision/catalog manifests and private evidence location are **UNKNOWN
until supplied/approved by the owner**. Never substitute social_manager or infer
permission from permission to run own mock tests. Approval for tests is separate
from approval for normal0095→0096 schema application and fixture preparation.

1. Pin current PR and fresh dev; verify exclusive lane and a sequential local Kilo
   R10 route. Stop on changed source, another test writer, dirty/uncertain state,
   unknown target/privileges or unavailable runner. Never read test-report into
   coordinator context. Do not repair/bootstrap/reset/drop/stamp/create_all.
2. On a separately authorized target, retain before evidence for sole0095,
   existing day/receipt/outcome/reservation rows, definitions, owners/ACLs,
   enabled guards, permissions/model types and sequences. Existing historical
   evidence must not be overwritten or relabelled. A target-specific rollback/
   retention plan is a prerequisite to DDL approval, not destructive downgrade.
3. Only after explicit DDL approval, normal repository0095→0096 once. Independently
   read back the one new table, required checks/FK/unique/index/sequence, two new
   functions/three enabled triggers, old object parity and env.py model/permission
   registration deltas. Empty new table and no old business-row changes expected.
   Do not ignore drift or retry a failed/uncertain upgrade automatically.
4. Only after separately approved synthetic fixtures on that target, prepare and
   run a bounded PG harness through Kilo. Keep all receipts/liabilities/evidence
   retained unless the owner explicitly approves a preservation-safe cleanup.
   The local executable PG harness is still to be prepared; this document does
   not claim that it exists or has passed.

| Scenario | Required observed acceptance |
| --- | --- |
| Fresh snapshot | One acknowledged owned commit contains exactly one day and one matching original packet; all values exact, day frozen. |
| Same-payer same-day identical race | One fresh insertion and one exact replay after payer serialization; one durable pair, no counters rewritten. |
| Same-payer same-day conflicting race | Only the winner persists; loser conflicts; winner evidence and current counters/freeze unchanged. |
| Distinct payer / scope mismatch | Separate payer rows isolate values; injected/missing/inactive payer refuses without writes. |
| Provision vs admission | Compatible payer→day ordering, no deadlock in compliant paths; fresh frozen day denies admission and creates no receipt/reservation. No HTTP. |
| Missing/disabled/incorrect provenance guards | Provision and replay fail closed on a separately approved disposable fixture; never disable guards on retained evidence or auto-repair them. |
| Direct packet mutation/binding | UPDATE/DELETE/TRUNCATE refused; mismatched day snapshot/FK/check constraints refused; statements roll back, evidence retained. |
| Second insert failure / cancellation | Actual rollback and server lock release; no durable half-pair after a precommit failure. |
| Commit/readback uncertainty | Never success on uncertain ack; independent readback distinguishes absent/durable pair; explicit same-packet replay only, no resend/activation. Any injected/synthetic fault is labelled as such. |
| Replay after current-state change | Original packet replay leaves separately approved current balances/counters/freeze untouched. No unfreeze API added just to arrange this fixture. |
| Legacy/manual day / history | Existing day without provenance refuses adoption; selected-day provisioning never resets, expires or refunds earlier liabilities. |
| Retention / migration parity | RESTRICT preserves day-linked evidence; ordinary downgrade refuses; prior rows/guards/sequences retained with expected measured deltas. |

Completion of this packet means **prepared**, not PG-tested or rollout-approved.
Keep PR Draft until required real-environment evidence is reviewed at its own exact
SHA; no Ready/merge, HTTP/client wiring, unfreeze, providers/runtime or deploy follows
from this source review and the additional11 no-DB checks.


## Access-model compatibility update — new validation required

Dev access commit `eb6cad6c56e78768b6482eba312082b849d24712` is authoritative;
owner-directed changes are not reverted. Baseline imports/model move to canonical
LLM domain, public facade additions preserve the explicit manager bootstrap,
and baseline definition is0097 parent0096. Access migration0096, access_catalog,
permission/role managers and grants remain unchanged. The provenance table is
internal uncatalogued storage: post-access registration does not create implicit
ModelType/Permission/grant rows for it. No generic baseline CRUD manager is bound.

This is a concrete changed import/migration risk; focused no-DB checks must be
collected/executed at the new composed SHA. Old36/122 and11 results stay at their
original pins and are NOT claims about this new tree. Real PG remains unperformed.

Current local handoff: refresh HEAD and guides; OpenCode is the primary local
pytest route, Kilo secondary only if BLOCKED. Selected database remains owner-
reported social_manager_pr180_migration, previously0095/public/starck. Reinspect
its actual revision/catalog/evidence read-only. It needs separately approved
access0096 acceptance before baseline0097; do NOT auto-apply an access data migration
as baseline setup. Capture the baseline before-manifest AFTER a reviewed access0096
state; then separately approve normal0096→0097. For baseline0097 the expected
role/permission/resource grants are unchanged; the static catalog excludes the
new internal table. Registration may still reconcile existing known resources;
measure all deltas and stop on unexpected changes. No reset/drop/stamp/create_all,
fixture cleanup, guards weakening, live activation or production action.
If old baseline0096 was applied anywhere, stop and preserve its catalog/rows/
revision evidence; no automatic numbering repair or reapplication is authorized.
The old PR180 preparation script/runner are not a PR197 harness and remain untouched.


### Executed post-access compatibility checks

Authoritative dev: `eb6cad6c56e78768b6482eba312082b849d24712`.
Fresh composed tested SHA: `6f38406d545c3b3ccb37d6edb9409ac9ae914091`.
Dev was included by normal merge `10a6fae408cede2d5ba7f1d499d1148c0fadc597`;
old PR/source/test pins remain ancestors, without squash/rebase/force-push.
Shared facade conflicts were resolved using dev as authority, then only additive
baseline exports were restored. Both ownership rows remain on the append-only board.

Same isolated sandbox venv/versions; clean test worktree. Explicit source imports/
configuration/session/inspection doubles; no real settings/.env, DBAPI/engine,
DB/provider or online migration. Changed imports and migration chain are the
specific reason to recollect/retest the affected PR197 checks; accepted PR180
checks were not rerun, relabelled or modified.

```sh
/data/baseline-venv/bin/python -m pytest tests/test_reviewed_budget_baselines.py tests/test_reviewed_budget_baseline_owned_session.py tests/test_reviewed_budget_access_compatibility.py --collect-only --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
/data/baseline-venv/bin/python -m pytest tests/test_reviewed_budget_baselines.py tests/test_reviewed_budget_baseline_owned_session.py tests/test_reviewed_budget_access_compatibility.py --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Collection exit0:56 tests (0.35s). Execution exit0: **56 passed, 135 subtests
passed, failed0, skipped0, warnings0** (0.56s). This IS a combined affected
no-DB run at this pin; it is not full application/authorization/ASGI/PG acceptance.

New compatibility coverage checks: immutable authoritative policy/catalog/roles/
registration/bootstrap/access0096; unique revision chain access0096→baseline0097;
uncatalogued baseline denied by shared permission policy even with platform/
workspace owner or bypass claims; no new workspace role grants; additive facades
and no generic baseline manager; alias identity without ORM redeclaration;
post-access registration with inspector/connection doubles does not register the
internal table; unchanged admission method AST. Baseline monetary/atomic/replay/
rollback/owned-helper and offline SQL checks now exercise canonical LLM models
and baseline0097. No implementation of interactive baseline authorization is
claimed: provisioning remains a trusted-internal unwired API.

An intermediate run at `b76101dcd81e511453cdb001f2a7dbb26d55f1f2` collected56
but failed25/passed31 (110 subtests passed, pytest exit1): the harness omitted the
unchanged manager initializer's supported analysis compatibility import, leading
to a missing-dotenv import error before settings execution. Corrected ONLY the
test import double; no authoritative manager/permissions changed and no real
settings/provider/DB was used to obtain PASS. Original assertion strength retained.
Final successful pin above supersedes that failed attempt, not its historical record.

Static preservation review: 220 paths changed by dev since9c7bf4d are byte-equal
to the authoritative dev or retain its deletions. Five shared paths have only
PR197 additive facade/document/ownership work; compatibility tests verify the
facade preservation. Access0096, access_catalog, model bootstrap, permission/role
code and model registration are untouched. Diff check exit0. This source review
is implementing-agent review, not independent approval of the access overhaul.

Remaining: independently reviewed access0096 target, separately approved
baseline0096→0097 application, executable new PG harness and real PG acceptance.
Owner-reported social_manager_pr180_migration0095 is historical; no new DB
inspection/migration performed remotely. No unfreeze, HTTP/client wiring or rollout.
Local tests follow current OpenCode primary / Kilo only if primary BLOCKED, not
old historical runner instructions. Preserve old PR180 evidence and target data.
