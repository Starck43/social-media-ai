# Single-part digest transport — bounded implementation handoff

## Current merged status

PR #9 merged into dev (`daf8e9c`) on explicit owner instruction. The preparation
status below is historical evidence, not current PR state. Foundation remains
opt-in; builder activation and production migration are not claimed. Current
continuation: [implementation status](../IMPLEMENTATION_STATUS.md).

## Historical preparation status and dependencies

Prepared in [PR #9](https://github.com/Starck43/social-media-ai/pull/9), branch
`ai/digest-single-part-transport`, stacked on contract PR #7, then schema PR #6.
Verified dev baseline: `7a0637412ae761fff83f26fb4f8b5a581dcf6e28`.
Code/test commit: `314738eff5968a390f5e0c6c6d6e4c301826a06d`.
**Prepared only, not merged/deployed; existing builder/broadcast/send unchanged.**
Neither this PR nor the pure helpers implement durable delivery by themselves.

## Implemented in this branch only

- Telegram/MAX opt-in `send_part`: supplied text is sent exactly once, without
  splitting/truncation or HTTP retries. HTML formatting is set on every part.
- Local invalid/oversized/disabled requests are blocked before HTTP. Length is
  a conservative raw UTF-16 bound, including markup/entities; it can reject a
  rendered-visible message that a provider might otherwise accept.
- Telegram acknowledgement requires HTTP 200, boolean `ok=true` and a positive
  integer message ID. Known rejection requires a matching explicit negative API
  response and HTTP status from the bounded rejection set.
- MAX acknowledgement requires HTTP 200 and a nonempty valid message ID in the
  existing supported response shapes. HTTP 400/401/403/404/413/422/429 denotes a
  request rejection. 408/409/5xx/redirects, malformed success and transport errors
  remain uncertain rather than definitely unsent.
- Only bounded result codes; no raw exceptions, provider descriptions, report
  content or credentials in transport results/logging on this new path.
- Cancellation propagates; a future caller's persisted in-flight intent stays
  unresolved. No automatic replay of that intent is authorized by cancellation.
- Checkpoint transition supports in-flight -> blocked for proven local refusal
  BEFORE HTTP only. Never map a timeout/unknown response to blocked.

## Validation

**142 passed, 1 warning**, focused run 4.46s: new single-part tests, existing
channel tests and checkpoint tests. Warning is the existing SQLAlchemy
`declarative_base` deprecation. Targeted black/isort on new helper/test and
checkpoint files, compileall and diff checks passed. Existing adapter files were
edited narrowly without repository-wide reformatting.
Full suite intentionally not rerun for this additive, unused transport path.
All HTTP was mocked. No live sends or production DB/schema changes.

## Historical checklist and exact next step

Checked boxes mean implemented/tested in this branch, **not merged into dev**.

- [x] Single-request transport and truthful per-part receipt/outcome contract.
- [x] Regression coverage for existing channel methods; no activation yet.
- [ ] Review dependencies #6 -> #7 -> #9; merge/retarget only on explicit owner
      instruction and recheck fresh dev. Do not independently merge this stack.
- [ ] Versioned deterministic HTML-safe splitter. Reconstruct and verify the
      COMPLETE part count/hash list for each frozen target before ANY resume;
      individual hash verification must not silently omit a missing tail.
- [ ] Caller owns per-chat/global pacing, bounded retry/backoff and scheduling.
      A single-part method does not sleep/retry on 429; do not loop immediately.
- [ ] Per-run PostgreSQL serialization and durable frozen snapshot/in-flight
      intent before HTTP; save each acknowledgement before selecting next part.
      Failed receipt persistence after acceptance requires uncertainty handling.
- [ ] Current workspace/binding authorization before each send; frozen IDs and
      hashes are not access grants. Revocation must stop delivery without adding
      new recipients to the frozen plan.
- [ ] Builder/job integration preserving original period and forced generation,
      no LLM regeneration on delivery-only retry, truthful partial job/UI result.
- [ ] Restart/concurrency/legacy/uncertain recovery policy and integration tests;
      full isolated suite at the integration checkpoint.

Start here, then read `digest_checkpoint_contract_handoff.md` and the design in
PR #5. Do not switch broadcast to send_part until frozen parts, authorization,
pacing and locked persistence are in place together.
