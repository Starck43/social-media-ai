# PostgreSQL digest checkpoint store — bounded handoff

## Current merged status

PR #11 merged into dev (`1b52c72`) on explicit owner instruction. The preparation
status below is historical evidence, not current PR state. Foundation remains
opt-in; builder activation and production migration are not claimed. Current
continuation: [implementation status](../IMPLEMENTATION_STATUS.md).

## Historical preparation status

Prepared in [PR #11](https://github.com/Starck43/social-media-ai/pull/11), branch
`ai/digest-checkpoint-store`, stacked on #10 -> #9 -> #7 -> schema #6.
Baseline dev `5d4a328bc2a0a2c90032339718e3aa7192b02455`; cloud/hybrid docs preserved.
Code/test checkpoint: `47dcb0ed52357366bbd903c91be8412eb2040ac6`.
**Not merged/deployed; existing builder/jobs/transports are not switched.**
The store operates on an ALREADY frozen checkpoint; it does not create one.
No extra migration, general outbox or end-to-end durable retry is implemented.

## Implemented in this branch only

- Explicit non-bypass workspace, owned-run precheck, expected generation, exact
  content and complete part-list verification. Foreign/unscoped IDs do not grant
  access. Legacy NULL/malformed history is rejected rather than seeded as unsent.
- Nonblocking schema/run-namespaced PostgreSQL session advisory lock on a
  dedicated connection. It remains held across each intent/receipt commit.
- Recheck current active workspace and exact frozen owned/active/digest-enabled
  binding before each intent. Changed channel/destination or flags stop the send.
  The check is at intent time; revocation cannot be atomically coupled to HTTP.
- Commit in-flight intent BEFORE returning the exact part payload. Caller must
  retain the lock around HTTP and immediate outcome persistence. A surviving
  in-flight intent is not automatically retryable after crash/cancellation.
- Commit each receipt/rejection/uncertainty/proven local block immediately.
  Acknowledgements remain recordable if binding/workspace is revoked after HTTP;
  this grants no new send. Never label a timeout as rejection or local block.
- Compare-and-swap protects content/history from noncooperating writes. Failed
  or cancelled writes poison the context and roll back, preventing accidental
  commit on a later read. A failed receipt commit after acceptance is uncertain.
- Unknown lock acquisition on cancellation invalidates connection. Cleanup
  rolls back/unlocks before pooling; cleanup failure invalidates the session.
  Expected identity and dedicated backend checks prevent reconnect-as-owner.

Session locks require cooperating publishers. The old builder does NOT acquire
this lock yet; the store must not be activated independently or advertised as
protecting all current sends. Do not share the store/connection between tasks.
Run-level status/error/message_id and job status are not changed by this unit.

## Validation

**234 passed, 1 warning**, 7.71s: 17 new PostgreSQL/lock-store cases plus HTML,
checkpoint, new/old transport and tenant-routing regressions. Includes concurrent
worker exclusion AFTER receipt commit; restart/abandoned intent; per-part binding
recheck; foreign run/binding; cancellation and unknown lock acquisition; commit
failure without accidental later commit; noncooperating CAS conflict.
Warning is existing SQLAlchemy declarative_base deprecation. Targeted
black/isort, compileall and diff checks passed. Full suite intentionally not
repeated for this unused opt-in path; it remains an integration acceptance gate.
No live HTTP/LLM calls or production DB/schema changes. Fixtures seed frozen
rows solely to test the store; they are NOT a production snapshot factory.

## Historical checklist and exact continuation

Checked boxes mean prepared/tested in this branch, **not merged into dev**.

- [x] Dedicated lock, per-part durable intent/outcome and tenant/binding guards.
- [x] PostgreSQL restart/concurrency/commit/cancellation/CAS regression cases.
- [ ] Review dependencies #6 -> #7 -> #9 -> #10 -> #11; retarget/recheck fresh
      dev as predecessors merge. Never merge without explicit owner instruction.
- [ ] Atomic first-run snapshot creation and generation assignment BEFORE HTTP.
      Do not infer existing NULL/partial legacy rows are definitely unsent. Agree
      legacy/forced-generation policy and retention of earlier delivery evidence.
- [ ] Bind jobs/retries to original owned run/window/generation, including midnight
      rollover and force retries. No rebuild/LLM charge on delivery-only resume.
- [ ] Activate one coordinated builder path: locking, current authorization,
      reconstructed frozen parts, single-part transport, immediate receipts and
      caller pacing/backoff together. All senders for that run must cooperate.
- [ ] Truthful partial/blocked/uncertain run/job/UI outcomes and explicit operator
      recovery. Do not turn ambiguous acceptance into an automatic duplicate.
- [ ] End-to-end two-target/part retry and revoked-binding tests; real job/window/
      force/legacy integration tests; full isolated suite at activation checkpoint.

Read this and previous three handoffs, then current builder/run manager and job
execution. Reuse implemented helpers; do not mistake fixture setup for atomic
snapshot creation or existing BaseManager writes for locking-protected delivery.
