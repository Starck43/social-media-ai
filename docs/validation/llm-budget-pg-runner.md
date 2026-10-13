# Budget reservation PostgreSQL runner — offline proposal

**Prepared, NOT EXECUTED / NOT ADMITTED TO PG.** This document is a validation
contract for the existing bounded Draft PR, not a new task queue or whole-gate
acceptance. The application stays UNWIRED. No DDL is implemented by this runner.

## Evidence and pins

- Application/style: `9dd748cef465e09df74cc98c07ecc65748b41a8c`.
- Starting PR head: `20993efcee71a10aaef6809df6579d8c59382a08`.
- Observed dev: `22dc7e2e485863c71b3b041ba0710212ee2a9565`.
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

Run namespace: `7417a3edbb5e42ce990c721c900d682f2`. The template contains the
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

**Pending offline local verification, no PG permission:** at the exact runner
SHA, verify syntax/compile/import dependencies without importing this runner's
application paths; Black/isort only new Python file, inspect any resulting diff
and repin a real successor commit if needed. Do not run the accepted31 tests or
any pytest/collect/import-app/PG step. Verify review `.venv` and report-path
isolation; preserve primary checkout and `local/agent-session-d841`.
A dependency/private API mismatch, missing role privilege, denied read,
unsupported target or unsafe plugin configuration is BLOCKED, never repaired
by bootstrap, system Python, permissions bypass or application refactor.

PR remains Draft: runner and target evidence pending. HTTP, settlement/release,
all-path accounting, production migration/cutover and whole economics gate
remain open. The Gate B/C approval sheet in the PR is still authoritative.
