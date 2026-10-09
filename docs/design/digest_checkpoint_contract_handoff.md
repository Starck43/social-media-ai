# Digest checkpoint contract — bounded implementation handoff

## Current merged status

PR #7 merged into dev (`053de6f`) on explicit owner instruction. The preparation
status below is historical evidence, not current PR state. Foundation remains
opt-in; builder activation and production migration are not claimed. Current
continuation: [implementation status](../IMPLEMENTATION_STATUS.md).

## Historical preparation status

Prepared in [PR #7](https://github.com/Starck43/social-media-ai/pull/7),
`ai/digest-checkpoint-contract`, stacked on schema PR #6
(`ai/digest-delivery-state-schema`, head `5f51ab5`). Both contain dev `7a06374`.
**Not merged, not deployed, not connected to the digest builder or transports.**
This is a pure ledger contract, not a completed durable delivery feature.
The main readiness gate PRD-01 remains open.

## Implemented in this branch only

- Version-1 JSON validation, explicit run/workspace/generation identity, content
  hash and deterministic splitter version. Legacy NULL and unknown/malformed
  history fail closed; they are never interpreted as all-unsent.
- Frozen binding/channel/normalized destination and ordered per-part payload
  hashes. Duplicate bindings/destinations and out-of-order histories are rejected.
  Neither credentials, raw exceptions nor report text are stored in this metadata.
- Copy-on-write transitions for reliable ORM JSON assignment; no nested mutation
  of the caller's object.
- Pending/proven-rejected -> in-flight -> acknowledged/proven-rejected/uncertain.
  Acknowledged parts cannot be resent through this state machine. Uncertain and
  blocked are terminal until a separately designed resolution policy exists.
- One next known-unsent part per target, in original order. In-flight,
  uncertain or blocked parts stop that target, while other targets can progress.
- Exact-part hash verification for a future transport caller.

No authorization is provided by an ID/hash match. The helpers do not perform
SQL writes, locking, HTTP requests, splitting, generation allocation or job
binding. A 'rejected' outcome requires affirmative transport evidence; a timeout
or generic exception must not be labeled rejected.

## Validation

Targeted tests only; full suite deliberately not repeated for this pure,
currently unused module. No live transport calls or production DB changes.
Final focused run: **43 passed, 1 warning**, 0.63s. The warning is the existing
SQLAlchemy declarative_base deprecation. Targeted black/isort, compileall and
diff checks passed. Code/test commit: `11f424a95400039f1d633a546a7bd332fc0ba73e`.
An initial test setup attempt failed because the local PostgreSQL server was
started on its default port instead of sandbox port 55432; no application fix
or weakening of fixtures was used. The server was restarted on the correct port.

## Historical checklist and precise continuation

Checked boxes here mean implemented in this branch, **not merged into dev**.

- [x] Pure checkpoint contract and targeted state-machine tests.
- [ ] Review/merge schema PR #6 with explicit owner instruction.
- [ ] Review this stacked unit; retarget to dev after schema merge and recheck.
- [ ] Versioned HTML-safe splitting, deterministic regeneration of the same
      part list and full-list hash/count verification before ANY resumed send.
      Per-part checks alone must not let a shorter list silently drop a tail.
- [ ] Persist frozen snapshot before first send; authorize each owned active
      digest binding and never expand the frozen recipient set during resume.
- [ ] Per-run PostgreSQL serialization; persist in-flight before HTTP and each
      acknowledgement afterward. A DB save failure after acceptance is uncertain.
      Helpers alone do not prevent concurrent workers or provide durability.
- [ ] Transport contract: send exactly one supplied part and classify responses;
      no hidden adapter splitting or fabricated acknowledgements.
- [ ] Builder/job integration: resume original run/window without another LLM
      call, including midnight rollover; retry one forced generation, not resend.
- [ ] Legacy/blocked/uncertain recovery policy and truthful run/job/UI outcomes.
- [ ] Restart, concurrency, ownership/revocation and partial-send integration
      tests; full isolated suite at the integration checkpoint.

Start by reading `app/services/digest/checkpoints.py`, its tests and the retry
plan in documentation PR #5. Integrate only after checking current dev and schema
PR status. Do not replace the sender with these helpers as if that were complete.
