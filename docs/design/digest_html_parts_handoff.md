# Deterministic digest HTML parts — implementation handoff

## Status and dependencies

Prepared in [PR #10](https://github.com/Starck43/social-media-ai/pull/10), branch
`ai/digest-html-parts`, stacked on #9 -> #7 -> schema #6.
Dev baseline: `7a0637412ae761fff83f26fb4f8b5a581dcf6e28`.
Code/test checkpoint: `2321706a367ede878c632d21424ad7d76e4038c7`.
**Not merged/deployed; existing splitter/send/broadcast/builder unchanged.**
This bounded unit is not end-to-end retry and does not close PRD-01.

## Implemented in this branch only

- `digest-html-v1:4000-utf16`: deterministic split of current renderer's b/i/
  blockquote HTML into independently balanced parts. Close/reopen active tags;
  preserve visible text, styles, whitespace and atomic entities/code points.
- Limit includes raw UTF-16 markup and wrapper overhead, conservatively fitting
  both single-part transports. Custom test limits do NOT represent the frozen
  default version. No paragraph/word/grapheme-cluster guarantee in v1.
- Reject malformed/unsupported tags, attributes, entities, nesting and invalid
  Unicode. Unrepresentable long whitespace/markup fails rather than strip text.
  This is not a general HTML sanitizer, a rich-editor splitter or link support.
- Verify the COMPLETE ordered count/hash list for every frozen target before any
  resume. A shorter list, changed/reordered/extra part or changed content fails.
- Reconstruct only the explicit known splitter version and verify all targets
  before returning parts; no fallback to today's splitter for unknown history.

This path is still opt-in. Identity/hash matching is not permission: a caller
must load the independently authorized run/workspace first. The frozen target
list must not expand. A future change to boundary behavior needs an explicit
version and recovery policy; do not silently reinterpret a persisted v1 ledger.

## Validation

**196 passed, 1 warning**, 5.17s: HTML parts, checkpoints, new single-part and
existing channel tests. Warning is existing SQLAlchemy declarative_base
compatibility/deprecation. Targeted black/isort, compileall and diff checks passed.
An initial test caught a trailing-newline-only last part; corrected with atomic
trailing-whitespace grouping and explicit regression tests. No test was weakened.
Full suite deliberately not repeated for this currently unused additive path.
Mocked HTTP only; no live sends or production DB/schema changes.

## Checklist and next session

Checked boxes mean implemented in this branch, **not merged into dev**.

- [x] Deterministic balanced renderer-subset parts and exact complete-list guards.
- [x] Text/style/entity/UTF-16 and missing-tail regression tests.
- [ ] Review dependencies #6 -> #7 -> #9 -> #10, retarget/recheck fresh dev as
      predecessors merge. Explicit owner approval remains required for merges.
- [ ] Per-run PostgreSQL serialization and durable frozen content/state before
      HTTP. Persist in-flight intent first, every acknowledgement immediately,
      and never automatically replay crash/timeout/failed-save ambiguity.
- [ ] Current owned active workspace/binding/digest authorization before each
      send, without adding current/new bindings to an existing frozen plan.
- [ ] Caller pacing/backoff and bounded schedule-level retry; transport does not
      sleep/retry on 429. Never turn a generic exception into proven rejection.
- [ ] Builder/job original-run/window and forced-generation binding; delivery-only
      retry must reuse summary/content without another LLM charge.
- [ ] Explicit legacy/uncertain/blocked recovery and truthful job/run/UI outcomes.
- [ ] Restart/concurrency/authorization/revocation integration tests and full
      isolated suite at integration checkpoint, before activating the new path.

Start from this branch and read all three previous handoffs. Confirm dev and PR
statuses; do not rebuild completed helpers, mark unmerged work done or switch
broadcast before locking/persistence/authorization/pacing are implemented together.
