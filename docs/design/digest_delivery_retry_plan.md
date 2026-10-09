# Digest delivery retry checkpoint design

## Status and evidence

Design only in this planning branch; synchronized with dev `5d4a328bc2a0a2c90032339718e3aa7192b02455`.
Cloud/hybrid planning documents from merged PR #8 are preserved. All dependency
branches contain this docs-only update; tested application inputs are unchanged
since the 196-test run on 7a06374. No repeat suite for documentation-only sync.
DigestRun/builder fields and call paths on dev still match `4b57c14` (the new UI changes are preserved). The new universal
assistant architecture includes a future controlled outbox; this digest-specific
checkpoint work must not be presented as implementing that general outbox.
The owner approved the dedicated JSONB-field option. The schema unit is prepared in [PR #6](https://github.com/Starck43/social-media-ai/pull/6),
`ai/digest-delivery-state-schema`, separately from this planning PR, not merged.
No receipt/resume behavior is implemented in this branch.
Pure checkpoint helpers are now prepared separately in [PR #7](https://github.com/Starck43/social-media-ai/pull/7),
stacked on schema PR #6: 43 focused tests passed, 1 existing warning. They validate
versioned snapshot/run/workspace metadata and part hashes, use copy-on-write
transitions, and conservatively stop in-flight/uncertain/blocked targets. They
are not merged or called by the sender; DB persistence, HTML-safe frozen parts,
locking, authorization checks and builder/job resume remain unimplemented.
Full suite was not rerun for this unused pure unit.
Opt-in single-part Telegram/MAX transport is prepared separately in
[PR #9](https://github.com/Starck43/social-media-ai/pull/9), stacked on #7.
142 focused tests passed, including existing channel regressions and checkpoint
tests. No hidden HTTP retry/split/truncation; malformed/ambiguous responses remain
uncertain. Existing sender is unchanged; caller pacing/backoff and durable
persistence/resume remain open. Full suite was not repeated. Read
`docs/design/digest_single_part_transport_handoff.md` on the PR #9 branch. Read the precise checklist in
`docs/design/digest_checkpoint_contract_handoff.md` on the PR #7 branch.

The current builder reuses a scheduled DigestRun row after failure, rebuilds the
aggregation/summary and broadcasts to every current target again. DigestRun has
content, one message_id, status/error and llm_cost, but **no per-target receipt
field**. A process-local map would lose its receipts on worker restart.

Job.result is JSON and survives mark_failed, but mark_done replaces it and
successful jobs can be cleaned up. It is job-scoped, not a persistent
DigestRun-owned delivery ledger; a new job/manual retry cannot automatically
assume another job's receipt history. Do not repurpose the human-readable error
or HTML content fields as a hidden checkpoint container.

## Approved storage decision

### Selected: dedicated DigestRun JSONB field

Add one narrowly scoped, versioned delivery_state field with a separate,
schema-qualified Alembic migration; no new table or provider/billing redesign.
Keep rendered HTML in the existing content field. Store only version/generation,
immutable target/part metadata, receipt/outcome/message IDs and bounded error
codes in delivery_state, not credentials or raw transport exceptions.

Separate schema delivery from retry behavior, verify the latest single migration
head and test upgrade/rollback on an isolated database. Existing rows have no
proven per-target receipt history; migration must not invent success receipts.
Production migration/deployment is a separate operator action.

### Alternative without schema changes: limited same-job retry

Use a namespaced Job.result checkpoint only for retries of the same existing
job. Preserve that namespace until the job finishes. Pass/validate job identity
explicitly, freeze its run/window and document cleanup/retention behavior.
This is an interim job-bound feature, not a complete DigestRun/manual/new-job
recovery ledger. It cannot close the complete PRD-01 acceptance gate.

If a strict no-schema constraint remains, agree this reduced scope explicitly;
do not silently disguise it as durable business-grade delivery.

## Required behavior for the durable path

1. Freeze the rendered digest, recipient set and deterministic part boundaries
   before first HTTP delivery. A retry uses that snapshot, not a new summary,
   new targets or a shifted period. Recheck authorization without expanding targets.
2. Persist acknowledged progress immediately after every successfully sent part.
   Successful recipients/parts never re-enter an automatic retry queue.
3. Recheck selected workspace, active owned binding and digest flag for every
   resumed send. Revoked/foreign destinations must not receive the frozen report.
4. Distinguish known rejection from an in-flight/ambiguous outcome. Persist an
   in-flight intent before send; a crash/timeout/failed receipt save after remote
   acceptance cannot be called definitely unsent. Avoid blind automatic replay;
   expose the uncertainty for explicit operator resolution. Exactly-once is not
   promised when the external API offers no idempotency guarantee.
5. Serialize publication/checkpoint updates for the same scheduled run. Queue
   SKIP LOCKED alone does not prevent stale workers or different jobs from
   publishing the same schedule/window. Review a bounded PostgreSQL locking
   approach without introducing a second queue system.
6. Bind job retries to the original run/window, including a retry after midnight.
   Existing payload/result fields can carry a validated run reference without
   adding another job column. Never use a foreign job/run ID as an access grant.
7. Force resend intentionally starts a new delivery generation; retrying that
   same forced job resumes its generation instead of forcing all targets again.
   Manual sends remain intentional distinct runs unless an explicit guarded
   resume API is separately added. Legacy partial runs without receipts require
   an explicit policy, not guessed success/failure per target.
8. Keep LLM usage honest: delivery-only retry must not pay for another summary;
   repeated actual builds/forced generations still accumulate their real cost.
9. Telegram/MAX chunking must expose individual acknowledgements and stable
   part indices. Existing adapter-level splitting plus one aggregate success
   result is insufficient for part-level resume. Validate HTML-safe boundaries.
10. Return truthful sent/failed/blocked/uncertain outcomes without silently
    converting missing recipients, missing credentials or partial delivery to
    success. Persisted status/UI feedback needs explicit acceptance.

## Acceptance and regression checklist

- [ ] Two owned targets: one succeeds, one fails; retry sends only the failed target.
- [ ] Multi-part target: retry resumes from the first known-unsent part, not part zero.
- [ ] Worker restart uses DB receipts and the original content/window/targets.
- [ ] LLM summary is not regenerated for delivery-only retry.
- [ ] Tenant override/foreign run/job ID and revoked binding cannot leak content.
- [ ] Concurrent/stale workers do not clobber receipts or duplicate acknowledged sends.
- [ ] Timeout/crash/DB failure after acceptance produces an uncertain outcome,
      not an automatic duplicate or a false delivered claim.
- [ ] Force retry, manual runs, period rollover and legacy runs follow their
      documented policy; no implicit migration of unknown receipts.
- [ ] Missing transport/recipient, response validation and message splitting
      retain truthful error handling and existing isolation tests.
- [ ] Approved schema upgrade/rollback and Alembic drift checks; fresh isolated
      focused/full suite. No production send as a substitute for regression tests.

Update [implementation status](../IMPLEMENTATION_STATUS.md) as each approved
unit lands. Done means merged into dev; prepared/in-review is not done.


## Latest bounded implementation checkpoint

[PR #10](https://github.com/Starck43/social-media-ai/pull/10), stacked on #9,
prepares versioned deterministic b/i/blockquote HTML parts and COMPLETE frozen
part-list verification for all targets. 196 focused tests passed, 1 existing
warning. Visible text/styles/whitespace and entities/code points are preserved;
unsupported HTML/unrepresentable whitespace fails closed. No word/grapheme or
general-HTML guarantee. Existing splitter/builder/send remain unchanged; full
suite was not repeated. Handoff: `docs/design/digest_html_parts_handoff.md` in
PR #10. Schema/contract/transports/parts are prepared only, not merged/deployed.
Next: locked durable writes, authorization and caller pacing, then builder/job
original-run resume and integration tests. End-to-end durable retry remains open.


## PostgreSQL persistence checkpoint

[PR #11](https://github.com/Starck43/social-media-ai/pull/11), stacked on #10,
prepares opt-in persistence for ALREADY frozen history: dedicated per-run session
lock across commits; current workspace/exact owned binding checks; durable
in-flight intent and per-part outcomes; full-list validation; CAS; poisoned
failed-write contexts and cleanup invalidation. 234 focused tests passed,
including 17 PostgreSQL store cases. Full suite was not repeated. No activation,
initial snapshot factory, job binding or new migration. Advisory lock protects
cooperating callers only; existing builder does not participate. Handoff:
`docs/design/digest_checkpoint_store_handoff.md` in PR #11. Next: atomic initial
snapshot/generation, original-run job binding, coordinated builder/pacing and
truthful recovery, followed by integration/full tests. PRD-01 remains open.
