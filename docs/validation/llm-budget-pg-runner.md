# Budget reservation PostgreSQL runner — current evidence and history

## Current owner-local normal migration closeout — reported PASS

The owner relayed a completed local normal Alembic migration report at exact
FULL SHA `f4c14c4da0fc212591ca0f88c35db3f8a20d87d3` (clean worktree).
This is **owner-local evidence**, not a remotely executed Mac migration or a
remote inspection of the private JSON artifacts. Remote isolated evidence and
its tested SHAs below remain unchanged; no historical checks were repeated.

- Approved target: `localhost:5432/social_manager_pr180_migration`, schema and
  sole revision placement `public`, DDL executor `starck`, project venv.
  Both dotenv mechanisms suppressed; actual Settings/SQL identity verified.
- Baseline provenance: structure-only clone of the accepted as-found legacy
  `social_manager.public` revision0092, actual version/service rows and measured
  sequences, with no business rows. Genuine normal0092→0093→0094 ran once each,
  with independent local post-step readback. This is not stamp/create_all or a
  fresh-schema historical-chain bootstrap. Source stayed unchanged.
- Accepted variance is narrowly limited to the `tenants.ck_tenants_plan`
  varchar-array versus element-wise text casts after dump/restore. Raw definitions
  and digests retained separately; original source snapshot unchanged; no general
  CHECK-ignore rule. Owner-local checks confirmed allowed values/types/NULL.
- Evidence caveat: pre-0093 evidence was overwritten by verification debugging
  and reconstructed, with equality to a previously recorded digest. It is not
  represented as an untouched contemporaneous before-file. Sequence `setval`
  is not rollback-safe merely because its phase used a transaction; actual
  sequence states were independently checked, never reset/reapplied.
- Fresh immutable before-0095 was measured and hashed before the migration:
  `before-0095-20261014T184014.json`, SHA256
  `5b2dc57c9714580f6bc5e5721165c4ed1585257fc62a53308ce047c1bc21f240`.
  Version was exactly0094; model_types28, permissions368, service sequences83/440.
- Normal repository command: project `.venv/bin/python -m alembic -c
  <worktree>/alembic.ini upgrade 0095`, **exit0**, executed once. An earlier phase
  invocation failed at source-expectation inspection (`sys.path`), before the
  migration; its before-artifact is retained, not an extra upgrade attempt.
- Independent local readback: sole revision0095; exact two new tables, two guard
  functions and three enabled reservation triggers, required FK/constraints/
  indexes/sequences; prior five receipt/outcome triggers and three functions
  preserved. model_types28→30 (IDs84/85), permissions368→384, service sequences
  83→85/440→456; old rows unchanged, deletions0, business/new tables empty.
  No unapproved catalog/ownership/ACL drift; source snapshot remained unchanged.
- Private after/summary filenames: `after-0095-20261014T184236.json` and
  `migration-evidence-0095-20261014T184236.json`, mode0600. Only digest prefixes
  were relayed for these two; full hashes/contents are not remotely verified or
  invented here. Before/after artifacts are retained outside Git.

This closes the requested **normal online0094→0095 rehearsal on this accepted
legacy clone**, separately from synthetic transaction/guard acceptance. It does
not prove production deployability, all possible0094 baselines, downgrade,
cutover/rollback, HTTP activation, settlement/release or whole PRD-03. No runtime,
provider/deploy, cleanup, trigger disabling or additional pytest was authorized.
Final fresh-head review/checks and integration are separate from this report.

## Earlier remote closeout — isolated PG PASS; online migration then BLOCKED

Tested FULL SHA: `d10bde4c3e63a5308e5ae61b8f46b87e63cf3c71`.
Preparation-script pin: `251e4aeb1533dadc3cea5d004e9a914ec057b502`.
Actual preparation execution SHA: `943d14a70e32b1c870ee06e49c17853712165ec4`.
These are real local owned commits; none is a relabelled historical test SHA.
Publication/merge and compatibility with fresh dev are not certified here.

The owner authorized continuing remote testing/preparation. A new sandbox-local
PostgreSQL15.18 cluster was initialized, with no access to any Mac or production
DB. Explicitly provisioned nonadministrative DDL login `starck` and NOINHERIT
runner `smai_pr179_probe`, no memberships; dedicated final database
`social_manager_budget_pr180_verified`, schema `budget_review_pr180`.
Role facts are measured on this cluster, not inherited from the Mac report.
Runner has CONNECT but no effective database CREATE/TEMP; schema/table/sequence
ACLs are verified exactly. No app runtime, Compose, provider, deploy or live call.

Project Python3.13 venv: unchanged requirements.txt plus httpx0.28.1, which was
missing from requirements.txt but already declared by pyproject.toml. `pip check`
passed. Both dotenv sources were suppressed; no private .env was read.
Direct remote pytest is the owner's scoped exception; local Kilo rules remain.
No .agent/test-report.md was read, edited or used as coordinator evidence.

### Completed checks at the repaired source

- New/changed no-DB safety scope: **57 passed,0 failed,0 skipped**,20 subtests;
  three disabled-asyncio-plugin config warnings. Historical31/accepted6 not run.
- Genuine READ ONLY preparation preflight; rehearsal PASS with four savepoint
  guard probes, full enclosing rollback and independent empty-target readback;
  one acknowledged preparation commit on the final target, postcommit readback.
- Separate runner `verify_isolated_target()` READ ONLY Gate B PASS.
- One acceptance node: **1 passed,0 failed,0 skipped**, all **14 journaled
  scenarios PASS**. Four warnings: three disabled-plugin config keys and inherited
  SQLAlchemy declarative_base deprecation. No warning was hidden by enabling plugins.
- Actual retained **11 receipts /10 reservations /18 days**; independent read-only
  counts agree. Days16 current+2 historical. Sequence advancement19/15/18,
  untouched outcome/tenant sequences, exact protected-record hashes preserved.
- Journal SHA256 `d2637a762268325d05c43764d870e7521b278a978a01811aec1a2660f13e1d2c`.
  Catalog SHA256 `f1e01af9bcda59eb0b3767778874f6b2293f3fcd98076a6bd5a1f22665a84c55`.
  ACL SHA256 `e7371625619ad83a2b464fecde7cb3931910ba9e073c12ce5ce061f58f6ea304`.
- Final read-only other-client count0; pools disposed. All data/manifests retained;
  no trigger disable, receipt cleanup, reset, drop, create_all, chain or stamp.
- Black/isort and source compile PASS; app/migrations unchanged from9dd748c.
- Reproducible public safe summary: `llm-budget-pg-isolated-evidence.json`.

Commands (private runner approval/manifest key supplied as process inputs):

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest   tests/test_budget_isolated_fixture_coherence.py   tests/test_budget_isolated_fixture_coverage.py   --noconftest -o addopts= -q --tb=short --no-header
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest   tests/acceptance/test_llm_budget_pg.py   --noconftest -o addopts= -q --tb=short --no-header
```

### Failed starts retained, not silently retried

Initial read-only preflight refused PostgreSQL inet::text host-mask notation;
no writes occurred. Corrected exact loopback handling accepts /32 or /128 only.
An earlier synthetic target `social_manager_budget_pr180` was prepared at07338ac,
then its READ ONLY runner verification refused asyncpg byte-valued pg_catalog
internal-char fields. It retains its18 tenants and all manifests; no acceptance
admissions were run there, and it was not reset/dropped. The final dedicated DB
was prepared after adding explicit ::text casts for relkind/typtype/tgenabled.
The two rehearsal catalogs were compared and are identical.

At943d14a, pytest refused its own built-in LegacyTmpdirPlugin class BEFORE
BudgetPackage ran, before approval/journal creation or any DB connection. The
fix recognizes built-in modules, classes and instances, while rejecting external
plugins. It did not rerun preparation or any owned admission. For this reviewed
reader-only descendant, provenance retains the ORIGINAL apply.json bytes/hash,
original execution SHA and all historical source hashes. All preparation and
migration bytes still match exactly; the historical and current CATALOG_SQL
must match; real Git ancestry is mandatory. The new runner SHA remains explicit
in its separate approval. Evidence is never rewritten to pretend preparation
executed at the newer reader commit.

### Historical remote migration boundary — superseded only by owner-local report above

The REAL repository migrations/env.py, real Settings and real Alembic graph
rendered `command.upgrade(config,"0094:0095",sql=True)` successfully. Network and
Engine.connect were blocked. The generated SQL contains exactly two tables, two
functions, three triggers, transactional BEGIN/COMMIT and the conditional0094→0095
version UPDATE; no earlier revision, baseline DML or destructive statement.
Ordinary downgrade raises the reviewed preservation-plan refusal. The SAME0095
definitions were exercised successfully on PostgreSQL in the isolated package.

**At that remote closeout, normal ONLINE Alembic0094→0095 was NOT VERIFIED / BLOCKED**, because no
approved reproducible database with genuine0094 migration history and its full
prerequisite catalog exists in this remote environment. The synthetic target has
no Alembic history by design. Do NOT insert/stamp a0094 row, use create_all, replay
0093/0094, bootstrap the non-bootstrap historical chain, restore the business-data
dump or call definitions as a substitute. Nothing here proves deployability.

To finish that distinct boundary, supply an existing dedicated nonproduction
0094 target or a provenance-backed sanitized0094 snapshot (schema and actual
history, not a hand-written version row). No credential in chat. Then:
1. READ ONLY identity/nonproduction/role/window, SHOW search_path, actual qualified
   version-table placement and exact0094 row; full prerequisite catalog parity,
   no0095 objects; durable protected data/catalog/ACL/sequence manifests.
2. Audit on_version_apply/register_model_types BEFORE approval: it scans the whole
   schema, can INSERT model_types/permissions for every missing registration AND
   DELETE stale registrations. Refuse any preexisting drift. Approve only the
   exact metadata rows/permission codenames for the two new models, with measured
   existing rows and expected ActionType-derived count; no blanket callback DML.
3. Target-specific reviewed DDL/DML plan: normal repository Alembic upgrade0095
   only, explicit process POSTGRES_URL and DB_SCHEMA with both dotenv sources
   suppressed. Preserve measured version placement/search_path; no routing guesses.
4. Independent readback of0095, exact new catalog and metadata deltas, enabled
   guards, immutable receipts/outcomes and all protected records/sequences. Any
   failed/ambiguous write is STOP and read-only reconciliation, not a retry.

PR stays Draft while this requested normal online migration boundary and fresh-dev
compatibility/review remain outstanding. HTTP, settlement/release, all-path
accounting and whole PRD-03 stay OPEN; synthetic ack loss is not network ambiguity.
PR137 primary caveats and all retained manifests remain unchanged.

## Historical remote no-DB proposal — superseded status, evidence retained

Everything until the owner-local history below describes the EARLIER no-DB
phase only. Its pending pins/permissions and 50-test result are not current status.


**Prepared, NOT EXECUTED / NOT ADMITTED TO PG.** This document is a validation
contract for the existing bounded Draft PR, not a new task queue or whole-gate
acceptance. The application stays UNWIRED. No DDL is implemented by this runner.

## Remote Variant B repair — no-DB evidence, uncommitted proposal

The submitted local bundle did not contain its claimed R1–R13 fixes. Remote
repairs were made in an isolated worktree based on FULL SHA
`2d5a7586337aeee5f652a288ebf7a03f3c75eac3`. **These are uncommitted source bytes,
not a new tested commit.** Preparation and PG execution remain unapproved.
Normal Alembic `0094 -> 0095` is **NOT VERIFIED**. No HTTP/provider/settlement,
release or whole PRD-03 completion follows from this work.

### Completed remote checks

The owner explicitly authorized a Linux project `.venv` installed from the
unchanged `requirements.txt`, and direct remote no-DB execution without Kilo.
This is a scoped owner exception, not a change to local Kilo/R10 rules.
Python3.13; requirements installation and `pip check` succeeded. No private
`.env` or credential from the Mac was used.

```
env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest \
  tests/test_budget_isolated_fixture_coherence.py \
  tests/test_budget_isolated_fixture_coverage.py \
  --noconftest -o addopts= -q --tb=short --no-header
```

Final exit0: **50 passed / 0 failed / 0 skipped**, plus11 unittest subtests passed.
Three warnings concern the disabled pytest-asyncio plugin's config keys; no
plugin was enabled to suppress them. An intermediate run after adding schema/type
scope checks had one failure in the new wrong-owner test fake; its missing canned
schema/type responses were corrected, and subsequent final scope passed.
Original31 and accepted6 files were not run or relabelled.

AST parse/compile, Black/isort checks passed for the four changed/new Python
files. Extracted normal shared-target-floor AST matches the original8869263
body exactly; normal-mode awaiting and revision refusal have separate no-DB
fake-session checks. This is NOT a PG runtime/parity certification.
New safety coverage blocks real socket and SQLAlchemy Engine connections; all
DB-shaped tests use controlled fakes. No DB connection, SQL, migration, runtime,
provider invocation, schema preparation, fixture insertion, commit or push was
performed remotely in this repair phase.

### R1–R13 disposition

- R1: owned connections/transactions, close/dispose on all branches; preflight
  READ ONLY from startup options before dialect handshake and first explicit SQL.
- R2: exact private phase approvals, code/source/target/window binding; apply
  requires the hashed successful rollback rehearsal and reviewed catalog digest.
- R3: no missing-table ACL queries; schema absence and DB-wide empty-target
  preflight; schema creation is explicitly enumerated inside approved preparation.
- R4: catalog-resolved separately qualified sequence identifiers; all five ID
  sequences checked/granted, tenant indexes included from the real model.
- R5: valid full probe seed shapes, distinct missing dispatch, inner savepoint per
  failure and outer always-rollback seed scope. Probes run only in rehearsal;
  the enclosing rollback also removes its newly created sequence objects.
- R6: measure effective runner ACL in the DDL transaction via role-qualified
  privilege functions; verify absence after rehearsal rollback and independently
  read back after acknowledged apply. Never connect as runner to rolled-back tables.
- R7: one colon-based required-privilege list; one raw measured ACL map/digest;
  database CONNECT/CREATE/TEMP versus schema USAGE/CREATE checked separately.
- R8: DB-wide relation/sequence/index/view scope, table-generated types, exact
  functions/guards, owners and role membership; extra permissions refused.
  Full reviewed catalog signature from rehearsal remains mandatory, not self-signed.
- R9: pinned commit AND source bytes checked before helper execution; real private
  manifest bytes/digest, source/endpoint/target/fixture/catalog/ACL bindings verified.
- R10: exclusive no-follow private per-phase artifacts, durable fsync including
  directory, intent and precommit evidence; acknowledge commit separately; any
  later failure stays incomplete and reconciliation-required, never auto-retry.
- R11: sanitized environment and both dotenv sources suppressed before app import;
  connect/statement/lock/idle timeouts; no engine pre-ping/retry repair path.
- R12: constant drift checks plus exercised no-DB safety guards and fake sessions.
- R13: historical evidence retained. Later malformed41-character main-report
  “SHA256” references are transcription errors, not new measurements; original
 64-character hash remains in the historical owner-local section below.

### Execution is still deliberately blocked

`PREPARATION_PIN` and the isolated template pin remain
`PLACEHOLDER-PREPARATION-COMMIT`, gates false/null. Do not replace this with a
file SHA256: the pin is a40-hex real owned commit that contains the script.
Commit sequence after source acceptance: preparation code commit A; runner/template
pin commit B referring to A; verify actual script/definition bytes and bind
execution SHA/source hashes. No self-referential commit hash and no pinning old
uncommitted source to the prior tested8869263 result.

A dedicated DB must be separately provisioned; this script never creates the DB
or roles. Proposed exact tuple remains localhost5432 /
`social_manager_budget_pr180` / `budget_review_pr180`, DDL login/owner `starck`,
runner `smai_pr179_probe`. Their existence/attributes in a remote PG target are
UNKNOWN; Mac role facts are not inherited. Both logins must satisfy the reviewed
nonadministrative role floor; runner has no memberships/ownership. Provisioning
must establish CONNECT but no effective CREATE/TEMP for the runner, including
PUBLIC/default grants. All such provisioning operations need explicit review.

Preparation uses its OWN private approval, separate from runner approval. Required
fields: kind=`isolated-budget-preparation-approval`; frozen run_id; exact DDL target,
schema and runner_role; preparation_sha, execution_sha and source hash map;
nonproduction/exclusive_window attestations; UTC day/current window; exact ordered
PREPARATION_OPERATIONS and per-phase owner_approval. Phase values are
`explicit-read-only-approved`, `explicit-definition-rehearsal-approved`, and
`explicit-definition-commit-approved`. Preflight may start with endpoint=null
and records the measured approved-local target identity; write phases require that
exact endpoint. Rehearsal needs preflight_sha256; apply also needs rehearsal_sha256,
catalog_parity_reviewed=true and the independently reviewed rehearsal catalog hash.
No unknown field is filled with an inferred approval.

Private files live in `.agent/private-budget-review/7417a3edbb5e42ce990c721c900d682f/`
with700 directories/600 regular files. Approved stage commands, only after gates:

```
.venv/bin/python scripts/prepare_llm_budget_fixture.py preflight --approval-file <private-plan>
.venv/bin/python scripts/prepare_llm_budget_fixture.py rehearsal --approval-file <private-plan> --allow-definition-application
.venv/bin/python scripts/prepare_llm_budget_fixture.py apply --approval-file <private-plan> --allow-definition-application --allow-commit
```

Preflight, rehearsal intent/result, apply intent/precommit/acknowledged/result
artifacts are unique and never overwritten. Preparation retains only five tables,
their sequences/indexes/types, five guard functions/eight enabled triggers and18
frozen synthetic tenants (proposed reserved IDs900001–900018); no day baselines or
probe seed rows. The separate runner still creates its exact11 receipts/10
reservations/18 days and accounts19/15/18 sequence steps. No stamp, create_all,
chain upgrade, trigger disable, schema reset/drop or receipt cleanup.

### Prior owner-relayed preflight incident

The owner-relayed local correction states CREATE SCHEMA budget_review, GRANT USAGE
and DROP SCHEMA budget_review CASCADE were all committed as starck outside the
read-only authorization. Earlier read-only/preservation PASS was retracted;
Gate B was not completed. These are reported local facts, not independently
verified remotely. No automatic restoration/cleanup is assigned. No credential
from that transcript is reproduced here; rotation remains an owner/admin action.

Older preparation/target/status statements below are historical and are not an
execution authorization or certification of this repaired source.


## Owner-local no-DB closeout — reported PASS, routing caveat retained

Tested FULL SHA: `8869263046368af840bbe5a9dc8c17c3e2c99eea`. Owner reports **6 passed / 0 failed /
0 skipped**, exit0, for the sole positional file
`tests/test_budget_acceptance_denials.py`: expected refusal class-only record,
unrelated unavailability and validation errors propagate, fresh admission and
replay are not refusals, and the expected class is required. This is new helper
coverage, not the accepted31 scope or PG acceptance; it was not rerun remotely.

The separately authorized local venv-link/diagnostic/new6 assignment was consumed.
Both reported commands used `kilo run "<message>" --dir
/Users/admin/Projects/social-media-ai-pr180-review --agent test-runner --format
json --file <task-file>`, with diagnostic task /tmp/diag_task_runner.md and test
task /tmp/test_task.md. **This skipped test-orchestrator**, contrary to the
required R10 route. Preserve the reported test result, but do not certify full
Kilo → test-orchestrator → test-runner → pare-test routing. No corrective rerun
is assigned. pare-test arguments were reported as
`--noconftest -o addopts= -q --tb=short --no-header tests/test_budget_acceptance_denials.py`;
interpreter was review-worktree/.venv/bin/python. Plugin autoload was disabled,
PYTEST_PLUGINS/PYTEST_ADDOPTS/-p/conftest absent; exact pare project_root was not
provided and is not inferred.

Report-path isolation is owner-reported PASS **for the direct-runner invocation**:
review report SHA256
`d4818a497c585a2190cc9e5c9eb481dad8dd4b968ea7796fa31336e1d44385f8`;
main report unchanged at
`cb667f0df6cdce2b653f9bf3e29194fc3e0f46492edd285b99c002aad1d81801`;
no reports in other worktrees, no remaining pytest/children. The human-audit
report was not read into coordinator context. This does not prove orchestrator
report isolation or reuse for another lane's plugin/bootstrap problem.

Project-venv provenance was reported verified via the authorized review .venv
symlink to /Users/admin/Projects/social-media-ai/.venv; no install/bootstrap.
However, reported sys.executable.parent is the **main** .venv/bin, whereas the
PG guard requires literal **review ROOT/.venv/bin**. The report also calls .venv
untracked, whereas the PG guard demands empty git status. Thus strict PG
entrypoint/clean-worktree admission remains unproven; no guards are weakened.

**PAUSED / no further runs assigned.** Owner-reported connections/SQL/provider
calls: 0/0/0; no tracked changes, no active pytest/children. No social_manager or
historical test_schema access/write was reported. No DB writer window was
assigned to PR180; this is not permission to resume another lane. Target,
actors/privileges, actual fixture IDs, catalog/revision and exclusive PG window
remain UNKNOWN/unapproved. Gate B/C and migrations stay closed. PR stays Draft,
UNWIRED; HTTP, settlement/release and whole PRD-03 remain open.

Fresh dev observed: `aac8bfabe66010b38858102c0746776315ec367f`, not the
historical22dc7e2 pin. No dev merge or compatibility certification; PR176/151,
other lanes and local/agent-session-d841 remain separate. Current pause/status
is recorded in the PR description for [PR176 coordination](https://github.com/Starck43/social-media-ai/pull/180#issuecomment-6165882422).
Older NOT RUN/absent-venv/unverified-report statements below are historical
checkpoints, superseded only for the no-DB scope and direct-runner evidence above.

## Evidence and pins

- Application/style: `9dd748cef465e09df74cc98c07ecc65748b41a8c`.
- Starting PR head: `20993efcee71a10aaef6809df6579d8c59382a08`.
- Historical observed dev: `22dc7e2e485863c71b3b041ba0710212ee2a9565`; fresh observation above.
- Original no-DB evidence remains **31 passed / 0 failed / 0 skipped at
  `ff29f82615413533121c5265eec0477e02643f9a`**; never rerun/relabel it here.
- Runner commit is pinned separately in the PR handoff after publication. The
  private approval must contain that FULL SHA, or the later explicitly reviewed
  formatter-only successor. Runtime refuses dirty worktrees or any `app/` or
  `migrations/` delta from the style pin.
- [Primary receipt PG report](https://github.com/Starck43/social-media-ai/pull/137#issuecomment-6130408067)
  stays attributed to `e8d335ef17cf4b851aaded5a30c3d743b454263e` / 143 checks.
  Definition0093 was committed once; revision0092/journal79 were historical,
  not current facts. Synthetic catalog identities, committed trigger
  disable/restoration, interrupted-run recovery, retained44 receipts (7 final)
  and initial non-network injected commit-loss caveats are not superseded.
  No repetition, cleanup, definition workaround or guard disable is authorized.

## Formatter v2 received and published — no test permission implied

Latest runner/source FULL SHA: `8869263046368af840bbe5a9dc8c17c3e2c99eea`, parent
`bee661c7c4e727ef787ad9fee6ed6f7b8596548f`. Exactly one file +4/−12:
four negative-helper call wraps collapsed, no argument/logic changes.
Received2315-byte patch SHA256:
`113987c6ab812ab794f2ec5c7ca4cd4ce1442f4c82b6099b3c9a47b8e310d2ca`.
Base d012a58e's Python source is unchanged in bee661c; clean exact-base apply,
whitespace and scope checks passed. Published file matches attachment exactly.
Git blob `67f6b3900cf734710fc24a890e949a4e97c11d5f`;
file SHA256 `4bd089e55e8c26da1640bcb084697c9f50966d11e923cbda24667655dd7e8e5a`.
Remote lexical tokens before/after are identical; this is NOT Python AST
execution. No application/migration, manifest/identity or six-check file changes.

Owner reports Black/isort and AST parse exit0 on formatted source, then explicit
AST compile exit0 on both files; command rendering is abbreviated/flattened,
not independently reproduced remotely. Those owner-local results retain source
attribution, not a new remotely executed suite or PG certification. The prior
statement that bee661c reverted formatting is incorrect: that commit changed
only docs/manifest, and the local formatter patch had not been published.

New6 no-DB checks remain NOT RUN. Remote project .venv/Kilo absent; local review
.venv access and empirical report isolation still require a separate agreed
plan. Attachment receipt does NOT authorize the proposed symlink, diagnostic,
pytest, connection/SQL, target creation, role/grant or migration. No runner guard
is relaxed and no accepted31 or old style scope is rerun.

## Namespace correction and target proposal review — no SQL authorized

The initial namespace `7417a3edbb5e42ce990c721c900d682f2` at d012a58e and earlier
was33 hex characters, violating the runner's32-character guard. That is a
preparation defect, not evidence from an executed run. The frozen v2 namespace
is `7417a3edbb5e42ce990c721c900d682f`. All30 dispatch IDs were explicitly
regenerated in the approval template and must be reviewed as a NEW manifest;
there is no silent reuse/relabeling of the previous IDs. Fixture slugs retain
their existing8-character namespace prefix and underscore-to-hyphen mapping.
All18 tenant IDs remain null, all permissions closed. Prior manifest remains
in Git history. No DB/runner execution or retained-data cleanup was performed.
The runner Python source remains at d012a58e; this is a documentation/argument
manifest correction, not a code or tested-SHA successor.

Owner-relayed second offline report states AST parse and Black/isort exit0 on
locally formatted source; formatter v2 patch (+4/−12) was initially referenced only by an
owner-local path; the actual attachment is now published at the separate pin
above. The earlier clean-versus-changed explanation did not imply a remote
revert: the patch had simply not been published.
The shown AST command lacks a literal file-list definition and compile() call;
no compile PASS is inferred. New6 no-DB regression checks remain NOT RUN.

The submitted PG target proposal is BLOCKED, not accepted for execution:
- localhost:5432 nonproduction ownership was inferred from old PR137 history,
  not established currently. testuser existence/privileges are not verified.
- A fresh empty database has no0094 prerequisites. upgrade head implicitly runs
  a chain; no-chain/no0093/0094 reapplication wording cannot make it a lone0095
  step. migrations/env.py also loads dotenv and invokes model-type registration;
  explicit target routing and all side effects need separate review/approval.
- budget_review_run_ plus the old/full namespace exceeds40 characters; use an
  explicitly proposed short identifier, e.g. budget_review_7417a3ed (22 chars),
  never silently relax the guard. The actual schema remains UNAPPROVED/null.
- A NOLOGIN runner cannot authenticate directly as the URL username that the
  runner checks against current_user. DDL NOLOGIN ownership requires a separate
  named login executor/membership path; new role creation was not enumerated.
- Blanket table/default-privilege grants are not the approved least-privilege
  matrix. Sequence USAGE/SELECT/state reads and the explicit reservation
  TRUNCATE negative probe privilege were omitted. Protected manifest reads span
  all target-schema tables/sequences; enumerate their SELECT privileges too.
- Use fixture_slugs from the frozen template verbatim, not full-run-ID slugs.
  Provision tenants separately under an approved plan; do NOT pre-provision
  payer/day baselines: verify_empty_fixtures requires them absent and the runner
  alone inserts its approved18 days. provision_days does NOT create tenants.
- A venv symlink is not intrinsically read-only. Wrong-HEAD routing does not
  certify report writes. Canonical Kilo command requires message BEFORE flags;
  no diagnostic or environment mutation is authorized by the proposal.

Next return must include the actual formatter attachment/provenance and a
revised target/actor/privilege/prerequisite plan. Creation/SELECT/migration,
role/grant changes, environment linking and diagnostics remain unassigned.

## Refusal assertion precision fix — source-reviewed, execution pending

A subsequent source review found that the former `denied` helper accepted a
six-class exception tuple, allowing an unrelated setup/domain/validation failure
to masquerade as the expected refusal. Each of its 14 call sites now supplies
one required keyword-only `expected` exception class. Only that class is caught;
other exceptions propagate to STOP. The journal records only synthetic dispatch
identity and exception class, never the exception message.

| Negative case | Required refusal class |
| --- | --- |
| Changed ceiling, standalone receipt upgrade | BudgetAdmissionConflict |
| Changed receipt stage, cross-payer stored identity | ReceiptEvidenceConflict |
| Trusted-scope mismatch, inactive payer | TenantContextError |
| Missing/frozen/blank baseline, undefined missing/frozen | BudgetAdmissionUnavailable |
| Non-current day, older liability, capped no-bound, current unbounded liability | BudgetAdmissionDenied |
| Subquantum amount | ValueError |

`tests/test_budget_acceptance_denials.py` prepares six no-DB regression checks:
expected refusal produces safe class-only evidence; unrelated unavailability
propagates; unrelated validation error propagates; True and False are not
refusals; an expected class is mandatory. It extracts only the actual helper and
Blocked class via stdlib AST, without importing the PG runner/app or connecting
to DB. This new scope is not the accepted31 scope and does not rerun it.

These six checks are **PREPARED / NOT RUN**, and new syntax/formatter checks are
also NOT RUN remotely (project .venv and Kilo absent). Text-only checks confirm
all 14 call sites specify the expected class, unchanged manifest constant blocks
and whitespace; this is not Python AST/compile or runtime evidence.
The latest exact runner SHA is pinned in the PR handoff after publication.
Prior formatter PASS belongs only to its historical source pin below, not this
intentional helper behavior change. No equivalence-to-old-helper claim.

No admission/session/SQL/guard/environment policy or manifest identity changed.
Proposed successful retained/sequence deltas and operation list remain unchanged;
no PG or environment permission added. A separately agreed local environment/
report-isolation plan is necessary before a single targeted no-DB run through
Kilo → test-orchestrator → test-runner → pare-test. Its sole positional scope is
`tests/test_budget_acceptance_denials.py`, never the PG acceptance node, and
must use `--noconftest -o addopts= -q --tb=short --no-header` with plugins
reviewed/disabled before startup. No ordinary pytest or system-Python fallback.

## Accepted formatter-only successor (no PG execution)

Runner FULL SHA: `efe8553fbadf1233e92f0ab7cdb140e491926e60`; parent
`ad2f3971a951912d5a3a17039c13e0cd02e824b6`. Exactly one changed path:
`tests/acceptance/test_llm_budget_pg.py`, +486/−120. The supplied patch
applied cleanly to the exact parent and the published file matches it verbatim.
Git blob: `e4366f4b89c1d373abc8ed04a287d5504b8f2604`.
File SHA256: `1dd7449a4b5a0613e2778aff9bbfe927359765a028e1f2e12294530efd7eeeb5`.

Owner-local STYLE PACKAGE PASS used Black26.5.1 / isort9.0.1 and the existing
primary project interpreter: formatting and both rechecks exit0; AST/compile
and structural equivalence reported exit0. Equivalence normalization was
limited to contiguous import blocks in the same scope; all other AST nodes
compared structurally. The report's compile/AST commands are abbreviated,
not independently reproduced remotely. This evidence pertains to the supplied
formatted source based on ad2f397, not a pytest run of the subsequently
published commit. No new tested-HEAD or PG safety claim.

Remote confirmation: one-path scope, exact patch/publication byte match,
whitespace PASS, application/migration delta from style pin zero. Lexical
source review found only wrapping parentheses/trailing commas and one
contiguous import reorder, with no other non-delimiter token changes.
This is a diff review, NOT independent Python parsing/AST execution.
No remote project venv, formatter invocation, Python import, pytest or PG.

The initial FAIL below remains historical evidence. Formatter assignment is
complete; do not repeat checks without a changed risk. Runner guards,
scenario/identity manifests and proposed deltas are unchanged. Review .venv
remains absent, report-write isolation unverified, and target/roles/window/tenant
IDs UNKNOWN. No automatic environment linking/bootstrap, diagnostic test run
or Gate B/C permission follows from this style acceptance.

## Owner-relayed offline review at the prepared runner pin

Review source: owner-relayed report at FULL SHA
`ffc860f8f8556d80c0df4190564f7f29d83b007c`, clean review checkout;
application/migration delta from style pin zero. Overall **OFFLINE REVIEW FAIL**.
Project interpreter in the primary checkout was confirmed; review-worktree
`.venv` was confirmed absent. The runner's two venv demands intentionally
fail closed: do not delete/relax them or create/link/install an environment
automatically. Environment accessibility remains a separate owner decision.

Owner reports AST/syntax/compile exit0, Black --check exit1 and isort
--check-only exit1 on the new runner only. The report elides the complete
compile expression; no invented literal command or independent remote
execution claim. Source-only review describes guarded transaction/race/manifest
boundaries; it does not establish runtime/PG safety. Empirical report-write
isolation remains UNVERIFIED. Connections0, SQL0, pytest/collection0,
app/runner imports0, tracked changes0, commits/pushes0 were reported.

Next bounded assignment: isort then Black on the new runner only using the
existing project interpreter, recheck both, prove structural AST equivalence
before/after (normalize import ordering only), compile without imports and
inspect the exact diff/whitespace. No pytest, app import, PG, venv changes,
style waiver, guard relaxation or inherited import-drift repair. Return the
one-file patch as an attachment and exact command/exit evidence; no local
commit/push is assigned. Coordinator will review/publish a real successor
commit and repin it separately; never relabel original evidence.

## Gate A owner-relayed facts

The clean owner review worktree was reported at style pin, not the eventual
runner head: `/Users/admin/Projects/social-media-ai-pr180-review`.
The existing project interpreter is under the primary checkout
`/Users/admin/Projects/social-media-ai/.venv/bin/python`. This does **not** prove
that the required review-worktree `.venv/bin/python` entry point is accessible;
verify its linkage, prefix and dependency provenance offline before assignment.
Do not create/install another venv or bootstrap dependencies automatically.

Checkout Kilo configuration permits attachment reading and delegates only to
`test-runner`; the child's report write is restricted to `.agent/test-report.md`.
That permission/configuration is not empirical report-write isolation. Verify
that the actual report path is in the review worktree, not the primary checkout
or another lane, before setting `report_isolation_verified`. Never read the
human-audit report into coordinator context. Routing denial means BLOCKED,
not IDE/shell fallback. No diagnostic run was performed.

**Endpoint/database/schema, DDL/runner roles, catalog/revision and exclusive
window are UNKNOWN.** Gate A reported zero connections, tests and changes.
The historical shared `test_schema` is not authorized and this runner rejects
it (and `public`) outright. Do not infer a target from private `.env` files.

## Files and invocation boundary

- `tests/acceptance/test_llm_budget_pg.py`: one positional pytest node,
  `test_reviewed_package`, with **14 journaled scenarios** below. One pytest
  pass would mean this one node, not fourteen separately collected tests.
- `llm-budget-pg-approval.example.json`: public, deliberately unusable template.
  All target-dependent values/permissions remain null/false. Copy only after
  owner instructions into the ignored worktree-local directory
  `.agent/private-budget-review/`, with directory mode0700 and file mode0600.
- `BUDGET_PG_APPROVAL_FILE` identifies that private approval. `BUDGET_PG_URL`
  and `BUDGET_PG_MANIFEST_KEY` are private process inputs, never chat, task
  contents, logs or commits. Keep the manifest key across before/after and
  separately authorized reconciliation. No credentials are in the template.
- Start pytest with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; no `PYTEST_PLUGINS`,
  `PYTEST_ADDOPTS`, `-p`, third-party plugins or conftest. The node reviews the
  resolved plugin list, but cannot undo already executed plugin startup; verify
  the environment before the invocation. Collection imports stdlib only.
- Future authorized execution is ONLY Kilo → test-orchestrator → test-runner →
  pare-test, sequentially, using the project interpreter and positional path
  `tests/acceptance/test_llm_budget_pg.py::test_reviewed_package`, with separate
  arguments `--noconftest -o addopts= -q --tb=short --no-header`.
  Canonical orchestration remains
  `kilo run "<message>" --dir <worktree> --agent test-orchestrator --format json --file <task-file>`.
  **This reference is not a run assignment.**

## Separate approvals; no automatic repair

Gate B is a separately authorized, read-only preflight, NOT this write runner.
Confirm exact endpoint/database/schema, server identity, roles/privileges,
nonproduction ownership, forbidden working/production tuples, exclusive window
and absent competing pytest/writers. Read-only must be set before the first
query/connection handshake by the separately reviewed preflight mechanism.
No preflight mechanism or PG command is assigned here.

Gate B must establish ORM/migration catalog parity, not merely hash whatever
objects happen to be present. Capture baseline contents, revisions, ownership,
ACLs, trigger/function definitions, indexes, sequence/default bindings and
privileges. Review the runner's `CATALOG_SQL` serialization for the subsequent
approved parity digest; include schema ACL and sequence cache/increment checks.
Check dependency/ownership/default ACL/security attributes as part of the
independent parity review as well; the runtime signature is a drift detector,
not a replacement for that review. The runner rejects privileged runner roles
(superuser, BYPASSRLS or CREATEROLE) and any other database client connection.
Idle clients count too: exclusivity is stricter than absence of active pytest.

Gate C requires a new, explicit operation/fixture/delta approval after preflight.
The runner only accepts an already reviewed **normal revision0095** and exact
catalog parity. If normal0094 is verified, the owner's target-specific plan may
approve the normal0094→0095 migration separately, then establish its post-DDL
catalog baseline before this runner. If0095 exists, require exact parity and no
DDL. Any revision/catalog mismatch, manually applied legacy definition history
or unexpected0095 is STOP. No stamp, reapplication0093/0094, chain upgrade,
direct-definition invocation, schema creation/reset/drop or downgrade exists.

## Frozen synthetic identities and fixture requirements

Current run namespace v2: `7417a3edbb5e42ce990c721c900d682f`. The template contains the
exact 30 dispatch IDs, derived from SHA256(run namespace + ':' + identity key),
first32 lowercase hex characters. IDs must be compared verbatim before approval;
no UUID generated during execution. Existing IDs or a manifest at that namespace
cause STOP. Any revised namespace requires a new reviewed identity manifest.

18 fixture keys and exact synthetic slug identities are frozen in the template:
`fresh`, `race`, `replay`, `other`, `cap`, `missing`, `frozen`, `blank`, `old_day`,
`historical`, `free`, `undefined`, `unlimited`, `quantum`, `rollback_receipt`,
`rollback_balance`, `ack`, `inactive`.

**Actual tenant primary keys are NOT PROVIDED.** Do not invent them, map these
keys onto business tenants or silently create tenants. Gate B must verify
pre-existing, dedicated synthetic tenants with those slugs and no previous
receipts/reservations/baselines or unrelated business liabilities. If absent,
a separate explicit fixture-creation plan is needed before this runner; no
role/reference/catalog/tenant seeding exists inside it.

Fixtures use pro/column-cap1.0, except business/column-cap0 for `unlimited`.
`inactive` must already be inactive; all others active. The existing resolver
is real: the global fallback is explicitly process-local0, never persisted.
Synthetic model/provider snapshot IDs are18001/18002, no catalog INSERTs or
provider execution. These are preparation snapshots, not actual model usage.

All baselines are explicit reviewed inserts, not admission provisioning:
settled0 except fresh0.1 and undefined1.0; reserved0; both counts0;
frozen false except `frozen`; `reviewed-baseline-v1` except deliberately blank
`blank`. `missing` has no baseline. `old_day` gets only UTC day D−1; `historical`
gets D and D−1, with the older baseline reserved0.1. D/window remain UNKNOWN:
pin one current UTC day, start before23:00UTC, require at least15 minutes left,
and stop on a day/window boundary. No TTL/rollover refund exists.

## Exact scenarios and successful retained deltas

| Scenario ID | Observable contract | Additional retained receipt / reservation |
| --- | --- | --- |
| catalog | Approved target/role/revision/catalog parity and empty fixtures before writes | 0 / 0 |
| fresh | Owned acknowledged commit, independent observer, settled0.1/reserved0.4, bound/cap/version snapshots | 1 / 1 |
| distinct_race | Both real owned transactions pause before payer locking; one True, one denial, reserved0.6 | 1 / 1; exactly one of race_a/race_b |
| identical_race_replay | One True/one False; independent replay False; changed receipt/bound/version/cross-payer denied; standalone cannot upgrade | 2 / 1 including retained standalone negative fixture |
| payer_isolation | Trusted scope mismatch denied; other payer independently reserves0.6 | 1 / 1 |
| cap_serialization | Actual waiter/blocker PID relation observed before synthetic cap writer commits0.5; waiting0.6 admission denied | 0 / 0 |
| baseline_refusals | Missing/frozen/blank and non-current-day baseline fail closed | 0 / 0 |
| historical_liability | Explicitly labelled D−1 pair fixture is NOT fresh admission; current priced admission denied | 1 / 1 fixture-only |
| tariff_states | FREE0; UNDEFINED separate count at exhausted priced cap; missing/frozen/inactive denied; unlimited unbounded; subsequent fixture pro/cap1 denies unresolved liability; capped no-bound and subquantum denied | 4 / 4: free, undefined, unlimited, quantum |
| rollback_after_receipt | Real insert then injected failure; independent observer sees neither pair nor increment | 0 / 0 |
| rollback_before_commit | Real balance flush then pre-commit failure; neither pair nor increment | 0 / 0 |
| post_commit_ack_loss | Actual owned commit finishes before synthetic exception; observer sees one pair; retry False | 1 / 1 |
| guards | Rollback-only UPDATE/DELETE/TRUNCATE and four invalid binding INSERT probes require exact SQLSTATE | 0 / 0 |
| preservation | Exact retained content/counters, protected keyed hashes, catalog/revision and sequence comparison | 0 / 0 |

**Success ceiling: +11 receipts, +10 reservations, +18 payer/day rows, +0 tenants,
+0 model/provider rows.** Two retained synthetic tenant changes only: `cap`
column1→0.5, and `unlimited` business/column0→pro/column1; corresponding ORM
`updated_at` changes are permitted and observed. No other tenant field changes.
Final D balances: fresh0.4 (settled0.1), race0.6, replay0.6, other0.6,
quantum0.000000001, ack0.4; undefined count1/settled1/reserved0, unlimited
unbounded count1/reserved0; all other current baselines remain zero.
Older historical baseline retains reserved0.1. FREE/UNDEFINED zero ceilings
never claim zero actual provider charge; structured provider price arbitrates.

Expected sequence allocations on complete success, with verified increment1
and cache1: receipt19, reservation15, day18. This includes replay/conflict
INSERTs, two fault-injection receipt allocations, one rolled-back reservation,
and four rollback-only binding INSERT allocations. Sequence advancement is
not rolled back; compare effective before-state (`is_called` included), retain
both manifests and never reset sequences. Unexpected deltas mean STOP, not
adjusting counts to the outcome. Any interrupted/failed run has partial deltas,
not the success ceiling; reconcile read-only before a separately approved retry.

## Offline security review and limitations

Source-only review performed in the remote isolated copy; **no project `.venv`,
Python imports, AST/compile, Black/isort, pytest or PostgreSQL execution** here.
Whitespace and source/manifest consistency checks are reported separately in
the PR. This is NOT runtime safety certification or an accepted green runner.

Reviewed/fixed boundaries:
- Real root model registration and existing session factory; settings installed
  before model/engine binding. Both dotenv entry points suppressed; clean
  environment, no provider credentials, DEBUG off, logging disabled while run.
- No externally supplied admission session or fake outer rollback. Fault patch
  remains installed through the real owned context's commit boundary. Synthetic
  acknowledgement loss happens strictly AFTER real commit; no network claim.
- Two-session barriers precede row locks; race tasks have deadlines and are
  canceled/drained on failure. Cap serialization polls a real blocking PID
  relation, not a sleep-only assumption. Existing async engine receives driver
  server_settings timeouts at construction; no extra engine/session abstraction.
- Receipt insertion ahead of HTTP is never substituted for admission. Only the
  explicitly retained standalone/history negative fixtures bypass admission;
  they are labelled fixture-only and their rows/deltas are separately approved.
- No trigger disable, replication bypass, permission changes or receipt cleanup.
  TRUNCATE is table-wide even as a negative rollback-only probe: explicitly
  approve this exact operation and verify guards first. Missing-guard PG variant
  remains NOT RUN; inherited isolated coverage is not relabelled.
- Private journal uses exclusive-create, mode0600 and fsync before writes; JSONL
  contains synthetic rows/identities and keyed hashes, never URLs/passwords/raw
  business rows. Protected before/after hashes cover schema tables; only explicit
  fixture days/new dispatch IDs and two checked mutable tenants are excluded.
- Interrupted-run handling does not perform automatic reconciliation/rerun.
  It records STOP/partial evidence, drains tasks, closes sessions/disposes pools.
  Check backend/task absence separately before releasing the exclusive window.

**Next environment/approval boundary, no PG permission:** formatter successor
and historical owner-local syntax/structural-equivalence evidence are recorded
above; the newer refusal precision fix has its own pending checks.
Do not repeat old checks without a changed risk; the new helper and six-check
scope are the specific changed risk, not permission to repeat accepted31. Application import/dependency runtime
validation is NOT RUN. Verify review `.venv` accessibility and report-path
isolation only under a separately agreed offline plan; preserve primary checkout
and `local/agent-session-d841`. Name an existing dedicated nonproduction target,
roles/window and actual fixture tenant IDs before asking for separate Gate B
read-only approval. No accepted31 rerun, pytest/collection, app import or PG
operation is assigned by this checkpoint.
A dependency/private API mismatch, missing role privilege, denied read,
unsupported target or unsafe plugin configuration is BLOCKED, never repaired
by bootstrap, system Python, permissions bypass or application refactor.

PR remains Draft: runner and target evidence pending. HTTP, settlement/release,
all-path accounting, production migration/cutover and whole economics gate
remain open. The Gate B/C approval sheet in the PR is still authoritative.
