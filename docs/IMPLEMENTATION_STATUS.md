# Implementation status and session handoff

## Read this first

Latest checked dev: `57b5612f28bc8d9621cf21670babe8a4195d536e` (2026-10-09).
PR #12 merged as `72a57cb`; PR #16 as `c1bbc49`; PR #17 as `be333fe`; PR #18 as `4f01edb`;
PR #19 as `f0a4510`. Owner chat/notification UI update `1e908b2` is preserved. On 2026-10-09 the owner reported
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

## Dispatcher log privacy — merged in PR #20

PR #20 merged as `57b5612`; original branch `ai/dispatcher-log-privacy`,
baseline `1e908b2`. Runtime/digest outcomes and parallel UI are preserved.

- [x] Replace dispatcher-owned raw result/exception/traceback logs with bounded
  events, safe IDs and static categories; preserve severity and outcome decisions.
- [x] Fixed failure-notification template; no raw exception text in its message.
- [x] 13 actual-source privacy tests and 25 existing mocked-source outcome
  regressions PASSED locally. 2 PostgreSQL cases prepared, NOT executed.
- [x] Handoff includes commands, compatibility/diagnostic trade-offs and limitations.

See [dispatcher-privacy handoff](design/dispatcher_log_privacy_handoff.md).
Job.error/AgentTask.last_error and successful notification summaries are unchanged;
handlers/provider/ORM/framework logs remain outside this bounded change. This does
not close global privacy, queue or production gates.

Next small work: review one remaining handler-log path or audit task-outcome UI
with owner file coordination. Important separate packages: fail-closed identity,
queue leases/atomicity, atomic memory batches and per-attempt budget reservations.

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
  action_send contract and confirmations; no new live posting.
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

1. Fetch fresh dev and open PR heads; PR #12/#16/#17/#18/#19 are merged. Preserve the owner
   runtime guard and check parallel changes before edits.
2. Continue package 1 (identity/permissions) first; preserve digest integration.
   General queue leases/heartbeat/Job-task consistency are package 2; attempt-cost
   accounting and budget reservation design are package 3, with schema approval separate.
   Read the job delivery handoff before any later retry/sending edits.
3. Verify actual target migration state independently; 0087 merged is not deployed.
4. Run the isolated tests and record actual results/commit, not historical counts.
5. Keep force/legacy/uncertainty stops and frozen evidence intact. Do not restore
   old broadcast behavior for a checkpoint-owned run after flag rollback.
6. Request separate owner approval for merge/live activation as appropriate.

No production environment, database, credentials or live messenger was changed.
