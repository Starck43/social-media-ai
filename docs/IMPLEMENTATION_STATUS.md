# Implementation status and session handoff

## Current continuation — PR22 merged; general queue is next assigned lane

PR #22 **MERGED into dev**, history-preserving merge commit
`683c49e85918503119af47338200d8f762a52403` (parents dev a983a15 and
PRhead 56d810538aa15f9e391c43dfed75e77aada2d2a1), after explicit Owner Ready/merge
approval. GitHub closed/merged verified; Draft removed. Merge tree equals
approved PRhead tree exactly. app/tests/rules/migrations match tested482eacb;
no new executable delta or automatic post-merge retest requested.

OWNER completed full at exact 482eacbd84ce640bd25cfaf4c75bbe1695621495:
1612 passed, 11 warnings, 64 subtests, 738.25s, saved PYTEST_EXIT_CODE=0;
HEAD unchanged before/after, only untracked perr logs. Owner-local full log not
independently read by agent. GitGuardian Security Checks completed SUCCESS on
approved56d8105; zero legacy commit statuses. No invented test-CI pass or
independent warning triage. History of red/interrupted runs remains evidence,
not current acceptance: bounded separately approved one-row TEST orphan cleanup
with zero dependencies preceded final green; no future deletion/reset permission.

Integrated bounded safeguards: bound active identity/session, fresh tool/right
checks, actor/args/tool-bound one-use confirmation, narrow owner/service grants,
preview-only action_send botaction.view/literal dry_run=True, no publication/
PENDING transition. Owner queued/running/outcome UI, source/task escaping,
LLM-default management, test isolation/onboarding correction preserved.
Merged/regression-verified is NOT deployed, live activated or business/security
accepted. Broad /app XSS audit, durable approval/CAS and release/browser/live
acceptance remain OPEN. Do not restart merged dispatcher privacy or typed work.

Next assigned sequence: general queue PRD-05 lane, THEN per-attempt accounting/
budget-reservation DESIGN (schema approval separate). Before implementation
resolve fresh dev/open Drafts/board ownership; choose one bounded independent
queue contract and preserve Owner parallel changes. No new queue code prepared
in this integration-record task. PR29 deferred, PR30/31/32 occupied model-layout
lanes separate/unmodified; do not run/merge them automatically.

Existing .agent R3/R4 and R6–R9 remain binding: main dev checkout off limits,
existing per-PR worktrees, sequential shared local test_schema, no schema create/
reset/drop/stamp/migrate/Alembic without separate command. Agent prepares tests
only; ZERO agent tests/collection/app imports/DB/migrations/browser/live calls.
Owner main checkout was not changed; PR22 worktree/logs/branch retained, no
cleanup or automatic branch deletion. Fetch-only inspection may observe dev;
no instruction to switch/stash/commit Owner main or remove review worktree.

This FOUR-file post-merge documentation record is PR #33; Owner explicitly
approved its integration separately from PR22. Its actual merge state/SHA are
in GitHub PR33. No new docs/design files, code/tests/rules/schema changes or
replay requested. Future continuation uses fresh dev, not frozen session heads.


## Latest raw-log diagnosis and prepared import-isolation follow-up

Attached owner log for `706bfea` was selectively inspected: actual run directory
is the retained PR22 worktree, Python3.12.6, collected1554; final1486 passed,
68 failed,11 warnings,58 subtests,663.02s. Markdown renderer PASSED (line1413)
and is not a failed node. Raw tier traceback includes plan_arrange_user and
scoped inserts; earlier reconstructed traceback/list was inaccurate, not proof
of an old checkout. Focused owner41/41 and139/139 remain separately reported.
This is inspected owner evidence, NOT agent test execution.

Later tracebacks import current_tenant_id/handle_collect from mock_infrastructure
and JobManager() returns the fixture's SimpleNamespace without enqueue. These
specific doubles are declared by returned-job-failure source fixtures, reused
by dispatcher privacy. The dangerous global-import boundary is addressed in
`99c8294`: load actual source with module-local declared from-import doubles,
including late/relative imports; restore temporary private alias; notification
mocks patch only that module's map. No application/rights/UI/schema change,
no fake enqueue added or assertion weakened. Eight new regression methods
PREPARED/NOT RUN; existing outcome25/privacy13 assertions retained. Other source
fixtures are not globally rewritten; do not claim all68 failures fixed.

Early CLI/digest arrange failures occur before this producer group and are a
separate compatibility checkpoint, not dismissed as unrelated/pre-existing.
Next: owner runs8/25/13 standalone scripts and the ordered producer-consumer
pytest group in the handoff. Agent runs ZERO tests/imports/collections/DB/live
calls; AST/whitespace/ref checks only. Full-suite rerun deferred until the short
group is clean and remaining arrange failures are addressed. PR29 deferred;
PR22 Draft/unmerged, no deployment/acceptance claim. Older sections below are
historical checkpoints, not the current continuation.

## Latest PR22 verification and fixture continuation

Owner/local-agent evidence at exact `70f58a1`: standalone dispatch16 /
identity18 / confirmation19 / action9 PASSED; DB/API/web group 133 PASSED,
6 FAILED. Owner attributes the six failures to earlier PR code, not the last
two-file correction; they are still OPEN integration failures. Full suite
reported 1554 tests and timed out after ten minutes: NOT a completed pass,
not proof that only six failures exist. Agent has not rerun any tests.

Fresh dev `6f810a4` is included via `ef9c417`, retaining owner UI changes and
updated markdown asset test. Fixture-only `31618a5` prepares fixes for five
plan-tier arrange errors: isolated VIEWER actor, role_id and eager rights;
owner scope only around source/task writes, quota checks outside the scope.
No application/UI guard weakened; new fixture NOT RUN. Recheck renderer case
on this integrated code rather than replace owner UI/test files. PR29 deferred.

Next: owner fast-forwards the retained review worktree, checks the interrupted
test-process/schema state without automatic reset/drop, then reruns plan tiers
and web chat. If clean, complete the10-file regression group and investigate
full-suite progress/stalls with verbose output and a diagnostic thread dump.
Record exact tested SHA; timeout is not acceptance. PR22 remains Draft/unmerged.

## Read this first

Latest integration: PR22 MERGED into dev as `683c49e85918503119af47338200d8f762a52403` after explicit Owner approval. Merge tree matches approvedhead56d8105; executable code equals Owner-green482eacb1612 passed/11warnings/64subtests/saved exit0. GitGuardian success, no invented test-CI pass. Not deployed/live/security/business accepted. Next assigned general-queue lane, then spend-reservation DESIGN; PR29/30/31/32 separate. Agent ran no tests/schema/live operations.
PR #12 merged as `72a57cb`; PR #16 as `c1bbc49`; PR #17 as `be333fe`; PR #18 as `4f01edb`;
PR #19 as `f0a4510`; PR #20 as `57b5612`. Owner chat/notification UI changes and
chat asset fix `9d83c9a` are preserved. On 2026-10-09 the owner reported
that all tests pass. This is OWNER-REPORTED, not independently rerun: exact commands,
counts and tested SHA/logs were not supplied; do not attribute that report to the
subsequent UI commits, bootstrap/readiness or new dispatcher-privacy package.
Owner runtime guard `1d26c1b` is preserved; it is not a complete security gate.
Earlier continuation baseline was `081165a` after PR #13/#14 documentation work.
Cloud/hybrid planning from PR #8 and parallel application changes are preserved.
Prepared, merged, deployed and production-accepted are DIFFERENT states.
Checked boxes below mean merged bounded work, never automatic acceptance.

## Merged into dev

- [x] Business-readiness documentation review — [PR #2](https://github.com/Starck43/social-media-ai/pull/2), dev `a531a3c`.
  Research dispositions and revised local UX/production/future roadmap; planning,
  not implemented connectors/outbox capabilities.
- [x] Tenant-safe digest destinations and whole-build workspace scope —
  [PR #3](https://github.com/Starck43/social-media-ai/pull/3), dev `7f1d8c1`.
  Active owned bindings, no env destinations, bootstrap-only unscoped builds,
  within-call dedup and None identifier guard.
- [x] Workspace notifications versus fixed operator alerts —
  [PR #4](https://github.com/Starck43/social-media-ai/pull/4), dev `4b57c14`.
  Explicit owned recipient, catalog-only alerts, source-owned DB notifications,
  no raw exception in notification content, old send button hidden pending UX.
- [x] Combined historical PR #3/#4 validation: 130 focused tests; isolated full
  run 1075 passed, 1 skipped, 10 warnings. Alembic 0086 at that checkpoint.
  [Notification evidence](NOTIFICATIONS.md). This is not a rerun for PR #12.
- [x] Progress/roadmap reconciliation and migration order — PR #5, dev `234a23d`.
- [x] Cloud/hybrid planning — PR #8, preserved as planning, not deployed connectors.
- [x] Project map/navigation and documentation organization — PR #13, dev `f560840`.
- [x] Source-linked model-reference correction — PR #14, dev `081165a`.

Parallel analysis navigation, summary-derived headings and mention-axis labels
remain preserved. The universal-assistant direction, architecture and
[product handoff](design/product_direction_handoff.md) supplement this work.
Historical CA-01–04 already exist; read the proposal review before rebuilding.

## Digest foundations — merged, not automatically activated

- [x] PR #6 — nullable delivery_state JSONB and migration 0087, dev `0ae0a18`.
  [Schema review](design/digest_delivery_state_schema_review.md).
- [x] PR #7 — checkpoint metadata/state contract, dev `053de6f`.
  [Contract handoff](design/digest_checkpoint_contract_handoff.md).
- [x] PR #9 — exact one-part Telegram/MAX transport, dev `daf8e9c`.
  [Transport handoff](design/digest_single_part_transport_handoff.md).
- [x] PR #10 — deterministic HTML parts and full ordered hash verification,
  dev `0529956`. [HTML handoff](design/digest_html_parts_handoff.md).
- [x] PR #11 — PostgreSQL session lock, durable intent/outcome, CAS, poisoned
  failed writes and current binding checks, dev `1b52c72`.
  [Store handoff](design/digest_checkpoint_store_handoff.md).
- [x] Historical component validation: 234 focused tests, 1 existing warning,
  including 17 PostgreSQL store cases; merged component files matched that test
  checkpoint. Earlier schema full run: 1084 passed, 1 skipped on `7a06374`.
  Neither count is a test result for the new job/publisher integration.

## Digest job integration — merged, acceptance open

Status: **MERGED; DATABASE/INTEGRATION ACCEPTANCE AND ACTIVATION OPEN**.
[PR #12](https://github.com/Starck43/social-media-ai/pull/12) merged into dev.
Owner performed tests separately and reported all tests pass on 2026-10-09.
Commands/counts/tested SHA/logs and deployment sign-off were not supplied.
Live sender activation remains separately gated.

Merged bounded implementation (checked, NOT production acceptance):

- [x] Atomic NEW snapshot factory, preserving complete immutable content, owned
  targets, parts and generation. Prior agent checkpoint `3b4e380`: 250 focused
  tests, 1 warning, including 16 factory cases. HISTORICAL only; not rerun here.
  [Original snapshot handoff](design/digest_atomic_snapshot_handoff.md).
- [x] Original server-owned Job/run/window/generation reference; reference and
  snapshot commit together, not in separate crash-prone transactions.
- [x] Default-off checkpoint publisher, no delivery-only LLM rebuild, committed
  per-part receipts, known-unsent retry only and conservative ambiguity stops.
- [x] Schedule build coordination, heartbeat/claim fencing, retained known build
  cost on failed binding, no duplicate cost transfer; unknown usage remains NULL.
- [x] Claim-fenced Job outcomes, partial/blocked/uncertain detail, no false success
  notification, and legacy-builder/flag-rollback protection for checkpoint runs.
- [x] Prepared PostgreSQL integration cases: two-target partial restart after
  midnight, cancellation/in-flight, revocation, atomic rollback, foreign reference,
  lost claims, concurrent schedule locks, force refusal and unknown cost.

Latest continuation checks actually executed: **8 pure stdlib outcome tests**,
Python compilation, AST and whitespace checks. PostgreSQL/pytest/integration,
existing dispatcher/tenant regressions and the full suite were NOT run in this
sandbox. Do not add their results to the historical 250 count.

See the authoritative new [job delivery handoff](design/digest_job_delivery_handoff.md)
for scope, recovery policy, test commands, rollout/rollback and known limitations.
The earlier snapshot handoff describes the earlier bounded factory checkpoint;
its statement that builder/jobs were untouched does not describe this continuation.

## Typed digest/learn/reflect boundary follow-up — merged, acceptance open

PR #17 merged as `be333fe`; prior branch baseline was `1d26c1b`.
No model/migration/owner runtime-guard changes; no live calls or activation.

- [x] Strict bounded Pydantic summary, fact and reflection-operation contracts.
- [x] Learn evidence restricted to rendered user messages; invalid output keeps
  the watermark. A successful run does not consume unrendered rows.
- [x] Reflect rejects a whole invalid/foreign/duplicate-ID operation batch before
  the first write. Prompt advice remains a proposal, not an applied instruction.
- [x] Frame digest brief, transcript and memory as untrusted data; redact local
  error diagnostics. This is not proof against injection or a fleet-wide audit.
- [x] 44 contract/mocked-boundary tests actually passed locally; 5 additional
  PostgreSQL regressions prepared but NOT run. Existing/full suite still required.

See [typed-boundary handoff](design/typed_output_boundaries_handoff.md). PRD-07,
PRD-02 and PRD-03 remain OPEN: no atomic memory-write transaction, spend ledger,
interactive identity replacement or full injection/permission acceptance.
The research checkbox and merge do not turn this bounded work into a completed gate.

## Returned handler failure follow-up — merged

PR #18 merged as `4f01edb`; original branch baseline `be333fe`.

- [x] Explicit non-checkpoint handler status=failed is terminal, not done; preserve
  known reported cost, a bounded audit result and failed task status.
- [x] No success notification or unattended replay of declared failures.
- [x] Exception backoff, legacy counters/skips and checkpoint finalizer preserved.
- [x] 25 policy/mocked-source tests passed in the authoring sandbox; 3 PostgreSQL
  cases were prepared but not run there. Owner subsequently reported all tests pass;
  no precise test count/commands/SHA/log was supplied. These are distinct evidence sources.

See [returned-failure handoff](design/returned_job_failures_handoff.md).
PRD-05 remains open: no new claim fencing, task/job atomicity, durable notification
outbox, scheduler correctness or exactly-once/billing-attempt guarantee.

## Bootstrap/readiness follow-up — merged, acceptance open

PR #19 merged as `f0a4510`; original branch baseline `7175e47`.
All four requested small tasks are merged, not deployment/acceptance evidence:

- [x] Reject shared working/test schema when DB_SCHEMA is unset (effective public).
- [x] Redact check diagnostics, including credentials in query/fragment; no DB calls.
- [x] Register /livez and /readyz on every application; /health aliases readiness
  and returns 503 for a failed/timed-out DB probe. API process/DB only, not workers.
- [x] Reconcile merged PR #18 and owner-reported tests without closing release gates.

24 actual-source/mocked-infrastructure tests PASSED locally; 8 ASGI cases prepared,
NOT executed here. Full suite and real-driver/API acceptance for this new package
remain pending. See [bootstrap/readiness handoff](design/bootstrap_readiness_handoff.md).

## Dispatcher log privacy — merged, acceptance open

PR #20 merged as `57b5612` on the owner's explicit instruction. Original branch
baseline `1e908b2`; fresh base `9d83c9a` had no overlapping files.

- [x] Replace dispatcher-owned raw result/exception/traceback logs with bounded
  events, safe IDs and static categories; preserve severity and outcome decisions.
- [x] Fixed failure-notification template; no raw exception text in its message.
- [x] Historical authoring checks: 13 actual-source privacy tests and 25 existing
  mocked-source outcome regressions PASSED. No new run at merge; 2 PostgreSQL
  cases prepared, NOT executed. Full-suite/local acceptance remains pending.
- [x] Handoff includes commands, compatibility/diagnostic trade-offs and limitations.

Fresh continuation: [next-session task list](design/next_tasks_handoff.md).
See [dispatcher-privacy handoff](design/dispatcher_log_privacy_handoff.md).
Job.error/AgentTask.last_error and successful notification summaries are unchanged;
handlers/provider/ORM/framework logs remain outside this bounded change. This does
not close global privacy, queue or production gates.

Next small work: review one remaining handler-log path or audit task-outcome UI
with owner file coordination. Important separate packages: fail-closed identity,
queue leases/atomicity, atomic memory batches and per-attempt budget reservations.

## Staged retirement warning privacy — merged, acceptance open

PR #23 merged as `98e3aad` on the owner's explicit instruction. Original baseline
`a88cd30`; code `be3f67c`, tests/handoff `a332a98`. Fresh dev verified after merge.

- [x] Only `_retire_staged` failure warning uses fixed event/category
  and bounded source ID; no raw exception/traceback or content hash in its logs.
- [x] Seven actual-helper/mocked-storage tests written, NOT run. No new test
  acceptance evidence, PostgreSQL run, formatter run or production claim.
- [x] Integrated via [PR #23](https://github.com/Starck43/social-media-ai/pull/23).
- [ ] Owner/local focused/full-suite acceptance remains pending; seven helper
  cases remain unrun. Follow-up inspection observed GitGuardian/Kilo success
  for PR #23 head `a332a98` and docs PR #24 head `79aa1a8`. PR #23 reviews/threads
  were empty. GitHub checks are NOT application-test acceptance.

Parallel open PR #22 (identity/permissions) is NOT merged by this task. It shares
`docs/IMPLEMENTATION_STATUS.md` and `docs/design/next_tasks_handoff.md`, not this
package's application/test files. Preserve BOTH journal sections when syncing
that branch; do not overwrite its prepared identity work or redo this warning.

Return-zero fallback, success deletion counts, transaction/cleanup and cancellation
are unchanged. Dispatcher PR #20, UI and owner runtime guard are untouched.
Other handler/provider/ORM logs and stored errors remain open.
See [retirement-warning handoff](design/staged_retirement_log_privacy_handoff.md).
Next: owner/local acceptance evidence; the separate `_count_failed_staged`
warning package is now prepared below, not merged.

## Staged attempt-count warning privacy — merged, acceptance open

PR #25 merged as `eb49d1d`; original baseline `3373a1e`,
branch `fix/staged-attempt-log-privacy`, code `9bb566a`, tests/handoff `78a86be`.
Merged status verified; deployment/application-test acceptance remains pending.

- [x] Merged: only `_count_failed_staged` failure warning uses fixed event,
  bounded source ID and static storage-operation category, without raw exceptions.
- [ ] Nine actual-helper/mocked-storage cases WRITTEN, NOT RUN. Static AST and
  new-test whitespace/line-length checks completed; no formatter/test execution.
- [x] PR #25 merge verified; this does not certify local/full-suite acceptance.

Hash filtering/order/duplicates, attempt-count arguments, fallback zero, cleanup,
transaction and cancellation unchanged. No staged-row deletion, dispatcher/UI/
runtime-guard/collector outcome change. Prior PR #23/#24 remain merged; remaining
collect/analyze/prune/provider/ORM/stored-error privacy and production gates open.
See [attempt-warning handoff](design/staged_attempt_log_privacy_handoff.md).
Next: owner/local results and acceptance after the verified merge; afterwards
one separate prune warning or coordinated collect path. Preserve PR #22 journal
entries when syncing; no identity/queue work or sender activation bundled here.

## Identity/permissions boundary — draft PR #22, not merged

Branch `ai/identity-permissions-boundary`; original baseline `57b5612`.
Fresh dev `274cb2c` integrated into PR branch by `8f09a83`, after the earlier
`0234c21` integration `f28b70f`; documentation #26/#27/#28, privacy #20/#23/#25,
owner LLM tests and VIEWER fixture history are preserved.
Implementation checkboxes below mean PREPARED code, NOT merged/accepted.

- [x] Core anonymous denial, tenant/subject-bound source/task/scenario owner
  allowlist, authenticated API manager scope and explicit CLI/service authority.
- [x] Declared dispatch right checks in call_tool/execute; earlier 13 policy and
  10 dispatch methods prepared, NOT RUN by agent.
- [x] Runtime active resolved-tenant identity, eager bound User rights,
  full-turn permission scope, exact session/chat binding (`b1e5d5`).
- [x] Fresh per-dispatch authority, actor/session/role/contract/argument-bound
  expiring consent, one-use registry approval, no later batch effects after
  staging, protected other-actor cancel/stop (`f4f3fd`).
- [x] Real action_send registration, explicit botaction.view, literal dry-run
  only, PENDING preview with no approval/provider/transport effects (`ccb8905`).
- [x] 46 new methods: identity18 / confirmation19 / action9; DB fixture and
  PENDING assertions updated. All newly prepared checks remain NOT RUN.
- [x] Owner push `963bf4f`, narrowed fixture `4735859` and owner-reported focused
  rerun retained. User.role_id NOT NULL now satisfied with isolated VIEWER;
  owner's `4b6e450` role change preserved, deleted imports restored separately.
- [x] Prepared collect_now `source.analyze` declaration (`ec69981`), matching the
  existing web collection right. Six additional dispatch/declaration regressions
  WRITTEN, NOT RUN; no live collection, role/schema or dispatcher change.
- [ ] Owner verification of this new block; full-suite completion; remaining raw
  manager/unannotated tool and legacy arrangement coverage.
- [ ] Legacy NULL-role reconciliation/automatic linking and durable confirmation
  CAS/revocation fencing require separate contracts/approval. No exactly-once claim.

See [existing handoff](design/identity_permissions_handoff.md) for matrix,
commands, evidence and limitations; [board](design/README.md) for allocation.
Original injection guard, runtime_process, dispatcher/parallel handlers and
merged digest integration preserved. No personal router, schema/migration,
queue redesign, cost package, live sends or deployment. Draft remains unmerged.

## Parallel documentation — merged, acceptance separate

PR #26 navigation merged as `10212b5`; PR #27 conservative operator observation/
recovery runbook merged as `0234c21`. Eight tabletop scenarios are prepared, NOT
executed here; merge does not establish deployment/restore acceptance.

## Owner-reported local evidence — previous fixture, not the follow-up

- `python tests/test_bootstrap_readiness_unit.py`: 24/24 passed.
- `python -m scripts.setup_test_db --check`: same PostgreSQL database, working
  schema public and separate test_schema confirmed; no per-tenant role seeding.
- `python -m pytest -q tests/test_bootstrap_readiness_unit.py tests/test_api_health.py tests/test_setup_test_db.py`: 33/33 passed.
- `python -m pytest -q tests/test_dispatcher_log_privacy_db.py`: 2/2 passed with
  owner's previous fixture. This is NOT evidence for new setup `4735859`.
- `python -m pytest -q`: interrupted at 120 seconds, NOT a completed green suite.

OWNER-REPORTED, not rerun by the agent. Exact tested SHA/full logs/dependency
versions not supplied; count of unchanged warnings does not prove other tests
unaffected. Historical authoring results above remain separately attributed.

## Owner confirmation — narrowed privacy fixture rerun

Owner confirmed the focused rerun of `tests/test_dispatcher_log_privacy_db.py`
passed after the new isolated-actor/narrow-owner fixture was published. This
confirmation refers to the two parameterized privacy cases requested in the
latest handoff. Exact tested SHA/output was not supplied; do not claim a verified
head or attribute it to the full suite. Agent did not run tests.

Previous 24/24 bootstrap and 33/33 focused readiness reports remain separately
recorded. Full suite previously timed out at 120 seconds; no completed full-suite
result or acceptance of the remaining permission/identity gates was supplied.
PR #22 stays draft/open; no merge or deployment authority was given.

## Current owner round-trip — recorded without rerunning

Owner reports local setup --check, dispatcher privacy, identity boundary and
permission_scope checks passed; exact tested SHA/commands/counts/logs were not
supplied for this group. These are owner-reported focused results, not acceptance
of the newer runtime/confirmation/action block or `ec69981` collection gate.
Separately, owner reports **61 passed** across test_llm_client_factory,
test_ai_output_boundaries and test_learning after integration on dev `274cb2c`.
No complete-suite claim or exact invocation/log is supplied; do not rerun those
61 tests merely to repeat their evidence.

The owner's local dev has uncommitted chat.css, chat.html and test_web_chat.py.
These files were not modified by this continuation; local-only edits cannot be
certified from GitHub. Use an isolated review worktree, not switch/stash/reset
that dev. Merge `8f09a83` reconciled only the board conflict and retained all
incoming dev changes. New code is bounded to collect_now permission metadata;
new checks add six methods to the existing dispatch test file. AST/whitespace
and preserved injection-guard comparison completed; agent ran ZERO tests.

Next: owner validates the pushed PR head, sends SHA + command/result evidence;
review compatibility/raw-manager limits before Ready for review. Draft, merge,
deployment and acceptance remain separate. No extra handoff file was created.

## Test target — shared PostgreSQL, isolated schema

Owner confirmed common `POSTGRES_URL` with different schemas. Use `DB_TEST_SCHEMA`
for tests, never the working `DB_SCHEMA` (default public). A separate database is
OPTIONAL, not a requirement; do not provision one for this follow-up.
Existing conftest redirects the schema before app engines/models import. Avoid
parallel pytest processes sharing one test schema; no reset/drop command ran here.

## Deployment and acceptance still required

- [ ] Verify target migration 0087 BEFORE updated ORM code starts. Owner reports
  it applied and the local 0088 constraint experiment reverted; not independently
  inspected here. One authorized
  migrator, backup/staging check and correct POSTGRES_URL/DB_SCHEMA; merge alone
  does not upgrade a target database. No production migration ran here.
- [ ] Run isolated PostgreSQL tests and existing regressions, then full suite.
  Never target the working/production schema; a shared PostgreSQL database is allowed.
- [ ] Stage receipt commit failures, backend/lease loss during HTTP, concurrent
  workers and changed requests/permissions; examine real UI error/result feedback.
- [ ] Drain all old publishers before a coordinated explicit flag activation.
  Direct legacy builder is refused with the flag enabled. No mixed sender cutover.
- [ ] Review fleet-wide rate admission and Retry-After support. The initial caller
  has 0.6-second pacing and queue backoff; 429 requires operator delay. This is not
  a distributed limiter or a complete multi-worker production acceptance.
- [ ] Operator evidence-based uncertainty/force/legacy reconciliation remains
  manual. No receipt reset API or force-new-generation UI is shipped.
- [ ] Recovery/audit retention remains open: preserve original jobs; existing
  successful-job cleanup can prune their references. Frozen DigestRun receipts
  remain, but complete retention/billing-grade audit is not implemented here.

No exactly-once guarantee is claimed. PRD-01 is not closed until fresh end-to-end
acceptance; prepared guarded code is not evidence of safe live deployment.

## Remaining production/local work

- [ ] PRD-02 / UX-02: fail-closed interactive identity, tenant/global permissions,
  action_send contract and confirmations; no new live posting. (Merged PR22
  binds the real handler to a confirmed `botaction.view` read-only preview;
  `dry_run=False` is refused, no ledger transition/publication. Registry/expiry
  checks plus isolated fixture/outcome UI covered by Owner full482eacb1612
  passed; merged683c49e. Durable approval/CAS and release acceptance remain open.)
- [ ] PRD-03: complete spend accounting/reservations; approve billing-grade schema.
- [ ] PRD-04: production profile, readiness, one migrator, private DB/restore drill.
- [ ] PRD-05: general queue/scheduler correctness, leases and atomicity; investigate
  historically intermittent scheduler tests without weakening them.
- [ ] PRD-06: retention/privacy, cleanup and revocation coverage.
- [ ] PRD-07: remaining memory atomicity/concurrency, scenario-schema and poisoned-input acceptance; typed boundaries are merged.
- [ ] Notification recipient-picker UX, durable results, rate limits and full
  process-wide logging redaction audit.
- [ ] UX-01/03/04/05 and later UX/FUT packages: remaining roadmap scope.

See [production gates](BUSINESS_PRODUCTION_READINESS.md); this checklist neither
replaces acceptance criteria nor authorizes deployment.

## Resume in a new session

1. Fetch fresh dev and open PR heads; PR #12/#16/#17/#18/#19/#20/#23 are merged.
   PR #22 remains a separate open identity/permissions branch with journal overlap. Preserve the owner
   runtime guard and check parallel changes before edits.
2. Verify the new PR #22 identity/confirmation/preview block using its existing
   handoff; do not attribute earlier focused fixture passes to this revision.
   Continue remaining coverage/compatibility review. Owner priority: package 1 rights, package 2
   general queue/lease/heartbeat/Job-task consistency, package 3 attempt-accounting
   and budget-reservation DESIGN with separate schema approval. No personal router
   or digest reintegration. Preserve parallel privacy continuation independently.
3. Verify actual target migration state independently; 0087 merged is not deployed.
4. Run the isolated tests and record actual results/commit, not historical counts.
5. Keep force/legacy/uncertainty stops and frozen evidence intact. Do not restore
   old broadcast behavior for a checkpoint-owned run after flag rollback.
6. Request separate owner approval for merge/live activation as appropriate.

No production environment, database, credentials or live messenger was changed.
