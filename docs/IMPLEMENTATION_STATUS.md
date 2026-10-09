# Implementation status and session handoff

## Read this first

Verified dev baseline: `7a0637412ae761fff83f26fb4f8b5a581dcf6e28`.
The integrated delivery baseline is `4b57c14`. New product-direction docs and
parallel UI/theme/sentiment changes in `7a06374` are preserved; their inclusion
is not mislabeled as an application-unchanged baseline.
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

Status: **STORAGE APPROVED; SCHEMA, CONTRACT, TRANSPORT AND HTML PARTS PREPARED / NOT MERGED; DURABLE RETRY NOT IMPLEMENTED**.
Owner decision: one dedicated JSONB field on DigestRun plus a separate migration.
This approves schema preparation, not production migration or automatic PR merge.
Task branch: `ai/digest-retry-checkpoints`, created from the verified dev above.
The local planning snapshot was refreshed when the new product docs landed.

- Storage/schema decision **APPROVED**: nullable `DigestRun.delivery_state` JSONB,
  no new table; separate schema-qualified migration PR. See [design](design/digest_delivery_retry_plan.md).
- [ ] Merge the schema unit after review. Prepared and tested in
  [PR #6](https://github.com/Starck43/social-media-ai/pull/6),
  `ai/digest-delivery-state-schema`; it is not yet in dev. Two migration tests,
  PostgreSQL/SQLite coverage, schema/digest regressions passed (62 tests).
  Schema full run on fresh UI dev 7a06374: **1084 passed, 1 skipped**,
  10 warnings; local Alembic head/current 0087, no drift. Actual local upgrade
  0086 -> 0087 and isolated-schema rollback were checked. PostgreSQL storage
  remains JSONB; ORM uses a SQLite-compatible variant for existing tests.
  No production upgrade was run; schema remains pending review/merge.
- [ ] Merge the pure checkpoint contract after dependency review. Prepared in
  [PR #7](https://github.com/Starck43/social-media-ai/pull/7), stacked on PR #6,
  branch `ai/digest-checkpoint-contract`. Code commit `11f424a`: strict versioned
  metadata/identity/payload hashes, copy-on-write state transitions, no automatic
  replay of sent/in-flight/uncertain/blocked parts. **43 focused tests passed,
  1 existing warning**; formatting/compilation/diff checks passed. Full suite
  not repeated for this currently unused pure module. No sender/DB integration
  or additional migration. Handoff: `docs/design/digest_checkpoint_contract_handoff.md`
  in PR #7; schema and contract both remain unmerged.
- [ ] Merge the opt-in single-part transport after dependency review. Prepared
  in [PR #9](https://github.com/Starck43/social-media-ai/pull/9), stacked on #7,
  branch `ai/digest-single-part-transport`; code/test commit `314738e`.
  Telegram/MAX send exactly one part without hidden split/truncation/retry,
  return validated receipts or rejected/uncertain/blocked outcomes, and redact
  raw response/exception details. **142 focused tests passed, 1 existing warning**
  including old channel and checkpoint regressions; full suite not repeated.
  Existing sender remains unchanged. Handoff:
  `docs/design/digest_single_part_transport_handoff.md` in PR #9.
  Caller pacing/backoff, HTML-safe splitting, locking, persistence and resume
  remain open. This also adds proven pre-HTTP in-flight -> blocked handling.
- [ ] Merge the deterministic HTML/full-list unit after dependency review.
  Prepared in [PR #10](https://github.com/Starck43/social-media-ai/pull/10),
  stacked on #9, branch `ai/digest-html-parts`; code/test commit `2321706`.
  Balanced renderer-subset b/i/blockquote parts, raw UTF-16 bound, preserved
  visible text/styles/whitespace, atomic entities/code points. Entire ordered
  count/hash list and exact snapshot/version checked for ALL frozen targets.
  **196 focused tests passed, 1 existing warning**; full suite not repeated.
  Existing sender/splitter remains unchanged. Handoff:
  `docs/design/digest_html_parts_handoff.md` in PR #10.
  Unsupported HTML and unrepresentable whitespace fail closed; no general
  sanitizer or word/grapheme preservation claim. Durable retry still absent.
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

1. Fetch fresh dev and this task branch; inspect changes since `7a06374`.
2. Read this file, the retry design, `app/services/digest/builder.py`,
   `app/models/digest_run.py`, its manager, channel adapters and jobs/dispatcher.
3. Storage is approved. Review the schema branch/PR and its validation; confirm
   whether it was merged. Do not claim retry behavior exists merely because the
   field exists, and do not apply a production migration without a deployment step.
4. Inspect stacked PRs #7/#9/#10 and their handoffs before rebuilding checkpoint
   helpers, transports or HTML parts.
   Next unit after dependency review: locked durable snapshot/intent/receipt
   persistence with authorization and caller pacing/backoff, followed by
   original-window builder/job resume.
   The pure helpers do not provide authorization, locking or durability. Do not reserve
   a migration number until fresh dev's current graph has been checked.
5. Keep done/in-review/in-progress/blocked distinct; append test evidence and
   exact continuation point at each handoff. Never mark the full gate complete
   based on a passing bounded routing test alone.

No production environment, database, credentials or live messenger were changed.
