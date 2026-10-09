# Implementation status and session handoff

## Read this first

Verified continuation baseline: dev `081165a7aac1cbb5b2f093f6ead20a89e1ce28ec`
(PR #14 model-reference reconciliation, after PR #13 documentation navigation).
PR #12 was synchronized with this baseline before the continuation below.
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

## Current task — PR #12 continuation

Status: **IMPLEMENTATION PREPARED; DATABASE/INTEGRATION TESTS AND ACTIVATION OPEN**.
[PR #12](https://github.com/Starck43/social-media-ai/pull/12), branch
`ai/digest-atomic-snapshot`, remains unmerged and undeployed.
Owner accepted performing tests separately; this does not authorize live activation.

Prepared in this branch (unchecked because NOT merged/accepted):

- [ ] Atomic NEW snapshot factory, preserving complete immutable content, owned
  targets, parts and generation. Prior agent checkpoint `3b4e380`: 250 focused
  tests, 1 warning, including 16 factory cases. HISTORICAL only; not rerun here.
  [Original snapshot handoff](design/digest_atomic_snapshot_handoff.md).
- [ ] Original server-owned Job/run/window/generation reference; reference and
  snapshot commit together, not in separate crash-prone transactions.
- [ ] Default-off checkpoint publisher, no delivery-only LLM rebuild, committed
  per-part receipts, known-unsent retry only and conservative ambiguity stops.
- [ ] Schedule build coordination, heartbeat/claim fencing, retained known build
  cost on failed binding, no duplicate cost transfer; unknown usage remains NULL.
- [ ] Claim-fenced Job outcomes, partial/blocked/uncertain detail, no false success
  notification, and legacy-builder/flag-rollback protection for checkpoint runs.
- [ ] Prepared PostgreSQL integration cases: two-target partial restart after
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

## Deployment and acceptance still required

- [ ] Verify/apply migration 0087 BEFORE updated ORM code starts. One authorized
  migrator, backup/staging check and correct POSTGRES_URL/DB_SCHEMA; merge alone
  does not upgrade a target database. No production migration ran here.
- [ ] Run isolated PostgreSQL tests and existing regressions, then full suite.
  Never use a working/production database as the test database.
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
- [ ] PRD-07: typed digest/learn/reflect write boundaries and evidence.
- [ ] Notification recipient-picker UX, durable results, rate limits and full
  process-wide logging redaction audit.
- [ ] UX-01/03/04/05 and later UX/FUT packages: remaining roadmap scope.

See [production gates](BUSINESS_PRODUCTION_READINESS.md); this checklist neither
replaces acceptance criteria nor authorizes deployment.

## Resume in a new session

1. Fetch fresh dev and the actual PR #12 head; check parallel changes before edits.
2. Read this tracker and new job delivery handoff before changing retry/sending.
3. Verify actual target migration state independently; 0087 merged is not deployed.
4. Run the isolated tests and record actual results/commit, not historical counts.
5. Keep force/legacy/uncertainty stops and frozen evidence intact. Do not restore
   old broadcast behavior for a checkpoint-owned run after flag rollback.
6. Request separate owner approval for merge/live activation as appropriate.

No production environment, database, credentials or live messenger was changed.
