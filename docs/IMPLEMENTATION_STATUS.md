# Implementation status and session handoff

## Read this first

Verified dev baseline: `7fedbd3b769ab5c9239be6f3cf852ac9fd2745a9`.
Application/test code is unchanged from integrated delivery baseline `4b57c14`;
new product-direction documentation has been read and preserved.
This tracker separates merged work from proposals and work in a task branch.
A checked box means the bounded item is merged into dev, **not** deployed or
that its entire production gate is closed. Update this file and the roadmap
at each handoff; keep original research/archive documents as historical input.

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

## Current task — digest retry checkpoints

Status: **DESIGN PREPARED; STORAGE DECISION REQUIRED; CODE NOT IMPLEMENTED**.
Task branch: `ai/digest-retry-checkpoints`, created from the verified dev above.
The local planning snapshot was refreshed when the new product docs landed.

- [ ] Approve checkpoint storage and schema scope.
  [Decision/design](design/digest_delivery_retry_plan.md): one dedicated JSON
  field on DigestRun is recommended; schema work requires explicit approval.
- [ ] Persist one immutable digest snapshot and per-recipient/part receipts.
- [ ] Resume only unfinished known-failed parts without rebuilding the summary.
- [ ] Recheck workspace ownership/activity/digest flag on every resumed send.
- [ ] Handle in-flight/unknown outcomes conservatively; no exactly-once promise.
- [ ] Prevent overlapping publication from corrupting checkpoints or duplicating
  sends; agree the bounded locking mechanism with the retry design.
- [ ] Cover restarts, partial success, failed checkpoint writes, revoked bindings,
  period rollover, force resend and legacy runs lacking receipt history.
- [ ] Run focused/full isolated tests and schema/upgrade checks for the approved
  scope; record exact dev/task SHA and validation limits.
- [ ] Review task PR; synchronize with new dev; merge only on owner instruction.

Nothing in this list is marked completed just because this plan exists. PRD-01
is **partially implemented, still open**.

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

1. Fetch fresh dev and this task branch; inspect changes since `7fedbd3`.
2. Read this file, the retry design, `app/services/digest/builder.py`,
   `app/models/digest_run.py`, its manager, channel adapters and jobs/dispatcher.
3. Check the recorded storage decision. Until approval exists, **do not add a
   migration/column** or claim durable retry implementation.
4. Recommended next unit after approval: separate schema PR, then receipt/
   transport/resume implementation against that approved schema. Do not reserve
   a migration number until fresh dev's current graph has been checked.
5. Keep done/in-review/in-progress/blocked distinct; append test evidence and
   exact continuation point at each handoff. Never mark the full gate complete
   based on a passing bounded routing test alone.

No production environment, database, credentials or live messenger were changed.
