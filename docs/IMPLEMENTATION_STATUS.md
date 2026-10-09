# Implementation status and session handoff

## Read this first

Verified code baseline: `1b52c723271ccaf38dea8c8eaadefc8a14e87aa1` (PR #11 merge).
Cloud/hybrid planning documents from PR #8 are preserved. This tracker distinguishes
merged code, deployment and production acceptance. Checked boxes mean merged
bounded work, not a deployed capability or a closed end-to-end delivery gate.
Full schema suite: 1084 passed, 1 skipped on 7a06374. Latest component suite:
234 passed, 1 warning on the final store branch; merged application/test files
match that tested branch. No post-merge full suite or production migration ran.

## Merged into dev

- [x] Business-readiness documentation review — PR #2, dev `a531a3c`.
  Research dispositions, local UX vs production gates vs future scale, revised
  roadmap and fresh-dev recheck. This is planning evidence, not feature delivery.
- [x] Tenant-safe digest destinations and whole-build workspace scope —
  [PR #3](https://github.com/Starck43/social-media-ai/pull/3), dev `7f1d8c1`.
  Active owned digest bindings only; env destinations ignored; bootstrap-only
  unscoped operator builds; within-call dedup; None identifier guard.
- [x] Workspace notifications vs fixed operator alerts —
  [PR #4](https://github.com/Starck43/social-media-ai/pull/4), dev `4b57c14`.
  Explicit owned recipient; catalog-only admin alerts; source-owned DB failure
  notifications; no raw exception in notification content; old send button hidden
  pending recipient-picker UX. General process logging audit remains open.
- [x] Combined validation of PR #3/#4 and documentation reconciliation.
  130 focused tests passed; full isolated run: 1075 passed, 1 skipped,
  10 warnings (coverage disabled). Local Alembic head/current 0086 and no new
  upgrade operations. Details: [notification evidence](NOTIFICATIONS.md).

The user's parallel analysis navigation, summary-derived headings and mention
axis labels were preserved. The new universal-assistant product direction, architecture and
[product-direction handoff](design/product_direction_handoff.md) are planning
work, not implemented connectors/outbox capabilities. They supplement this
readiness task and were not overwritten.
Historical CA-01–04 capabilities are already present;
see the proposal review rather than rebuilding them.

## Current task — digest retry integration

Status: **FOUNDATION MERGED; END-TO-END RETRY NOT ACTIVATED / NOT COMPLETE**.
Owner explicitly approved merge of the existing PR stack into dev.

- [x] PR #6 — nullable delivery_state JSONB and migration 0087; merge `0ae0a18`.
  PostgreSQL/SQLite compatibility, round-trip and Alembic drift evidence are in
  [schema review](design/digest_delivery_state_schema_review.md).
- [x] PR #7 — versioned checkpoint metadata and conservative state machine;
  merge `053de6f`. [Contract handoff](design/digest_checkpoint_contract_handoff.md).
- [x] PR #9 — opt-in exact one-part Telegram/MAX transport; merge `daf8e9c`.
  [Transport handoff](design/digest_single_part_transport_handoff.md).
- [x] PR #10 — deterministic renderer-subset HTML parts and COMPLETE ordered
  count/hash verification; merge `0529956`. [HTML handoff](design/digest_html_parts_handoff.md).
- [x] PR #11 — dedicated PostgreSQL session lock, committed intents/outcomes,
  CAS, failed-write poisoning and current owned binding checks; merge `1b52c72`.
  [Store handoff](design/digest_checkpoint_store_handoff.md).
- [x] Component validation: 234 focused tests passed, 1 existing warning; includes
  17 real PostgreSQL store cases and prior component/tenant-routing regressions.
  Source files in merged dev match the tested component branch; no repeat full run.
- [ ] Deploy migration 0087 to the target database BEFORE starting updated app:
  `python -m alembic upgrade head`, one migrator with correct POSTGRES_URL/DB_SCHEMA.
  Merge alone does not migrate. ORM reads the new column even before activation.
  Production deployment, backup/staging check and migration remain operator steps.
- [ ] Atomically create first immutable content/target/part snapshot and generation
  before HTTP, with current workspace/owned binding validation. Do not infer legacy
  NULL/partial rows are definitely unsent. This is the NEXT bounded code unit.
- [ ] Bind retries to original owned job/run/window/generation; cover midnight and
  force retries, retaining delivery evidence and avoiding another LLM charge.
- [ ] Coordinate builder activation with every sender participating in the run lock,
  one-part transport, immediate receipts and caller pacing/backoff.
- [ ] Truthful partial/blocked/uncertain run/job/UI results and explicit legacy/
  uncertainty recovery; no exactly-once claim or blind ambiguous replay.
- [ ] Two-target/partial-part restart, concurrency and revoked-binding integration
  tests; fresh isolated full suite at activation checkpoint.

Existing builder/send/broadcast remains unchanged. The store only accepts an
ALREADY frozen ledger and does not yet create one. Foundation merge alone does
not close PRD-01 or justify switching to the new sender.

## Remaining production/local work

- [ ] PRD-02 / UX-02: fail-closed interactive identity, tenant/global permissions,
  action_send registry contract and confirmations; no new live posting.
- [ ] PRD-03: complete spend accounting and reservation design; approve schema
  before implementing a billing-grade ledger.
- [ ] PRD-04: production profile, readiness, one migrator, private DB, restore drill.
- [ ] PRD-05: truthful job outcomes, heartbeat/leases and scheduler atomicity;
  investigate the historically intermittent scheduler test without weakening it.
- [ ] PRD-06: retention/privacy and cleanup/revocation coverage.
- [ ] PRD-07: typed digest/learn/reflect write boundaries and evidence.
- [ ] Notification recipient picker/result feedback, durable delivery status,
  rate limits and complete process-wide logging redaction audit.
- [ ] UX-01/03/04/05 and later UX/FUT packages: use the roadmap's remaining scope.

See [production gates](BUSINESS_PRODUCTION_READINESS.md) for acceptance criteria;
this checklist does not replace them or authorize deployment.

## Resume in a new session

1. Fetch fresh dev and check changes after code baseline `1b52c72` (documentation
   PR #5 merges afterward). Do not rebuild the merged foundation helpers.
2. Read this tracker, retry plan and four component handoffs. Inspect builder,
   DigestRunManager, job handlers/dispatcher and JobManager before activation.
3. Verify actual target migration state separately: 0087 is MERGED, not evidence
   that staging/production was upgraded. Never run production migration implicitly.
4. Next bounded code unit: atomic fresh snapshot/generation factory. Then guarded
   original job/run binding, coordinated builder activation and pacing/backoff.
5. Legacy and force-generation policy, uncertainty recovery and truthful status
   handling remain open. Keep prepared/merged/deployed/accepted distinct and append
   each unit's tests/limits/commit and exact continuation.

No production environment, database, credentials or live messenger were changed.
