# Digest delivery retry checkpoint design

## Status and evidence

Design only; synchronized with dev `7fedbd3b769ab5c9239be6f3cf852ac9fd2745a9`.
Application fields/call paths remain those of `4b57c14`. The new universal
assistant architecture includes a future controlled outbox; this digest-specific
checkpoint work must not be presented as implementing that general outbox.
No retry implementation, schema edit or migration has been made.

The current builder reuses a scheduled DigestRun row after failure, rebuilds the
aggregation/summary and broadcasts to every current target again. DigestRun has
content, one message_id, status/error and llm_cost, but **no per-target receipt
field**. A process-local map would lose its receipts on worker restart.

Job.result is JSON and survives mark_failed, but mark_done replaces it and
successful jobs can be cleaned up. It is job-scoped, not a persistent
DigestRun-owned delivery ledger; a new job/manual retry cannot automatically
assume another job's receipt history. Do not repurpose the human-readable error
or HTML content fields as a hidden checkpoint container.

## Storage decision requiring owner approval

### Recommended: dedicated DigestRun JSON field

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
