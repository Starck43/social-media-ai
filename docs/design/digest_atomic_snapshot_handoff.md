# Atomic fresh digest snapshot — bounded handoff

## Status

Prepared in [PR #12](https://github.com/Starck43/social-media-ai/pull/12), branch
`ai/digest-atomic-snapshot`, directly from merged dev `234a23dd7d1d2ce8dd3813f1ed63c7c7ef748669`.
Foundation PRs #6/#7/#9/#10/#11 and tracker PR #5 are merged on owner instruction.
Code/test checkpoint: `3b4e3805811b46231b7212cddffadf34db3587d1`.
**This new unit is prepared, NOT merged/deployed; builder/jobs remain unchanged.**
No new migration or production operation. Foundation migration 0087 must already
be applied to the target database before starting the updated ORM application.

## Implemented in this branch only

- NEW DigestRun content, complete owned active target/part snapshot and fresh
  generation committed in one transaction. Allocated IDs/incomplete rows are
  invisible to other sessions; any validation/commit failure rolls back creation.
- Explicit non-bypass workspace; current active workspace and owned digest
  schedule checks. Stable binding-ID ordering and exact normalized dedup.
- Current day inclusive start=end and week/month supported. Supplied bounds are
  frozen, never recomputed from the clock. Supplied actual LLM cost is retained;
  unknown is not zero, and this does not implement billing-grade accounting.
- No env targets/credentials, silent recipient drop or live HTTP. Invalid/empty
  configuration stops creation. Store can load/authorize the committed snapshot.
- Same schedule/window collision fails explicitly without touching old content,
  status or receipts. NULL/partial legacy history is not seeded as all-unsent.
  Separate manual calls deliberately create separate runs/generations.

There is no job-binding/resume/force API here. NEVER call this factory again on a
retry without first validating its original run reference. Do not pay for another
summary before checking an existing bound run. Initial forced-generation policy
and preservation of earlier receipts need separate integration, not overwriting
an existing unique schedule/window row. Upstream source/content permission and
summary-build coordination remain the caller's responsibility.

## Validation

**250 passed, 1 warning**, 8.41s: 16 new PostgreSQL snapshot cases plus delivery
store/HTML/checkpoint/channel/tenant-routing regressions. Includes uncommitted
row invisibility, complete rollback after ID allocation, parallel same-window
collision, legacy no-overwrite, owned schedule/targets and store compatibility.
Warning is existing SQLAlchemy declarative_base deprecation. Targeted
black/isort, compileall and diff checks passed. Full suite intentionally not
rerun before activation. No live HTTP/LLM or production DB migration ran.

## Checklist and exact continuation

Checked boxes mean prepared/tested in this branch, **not merged into dev**.

- [x] Transactional NEW snapshot/generation factory and PostgreSQL evidence.
- [ ] Review this PR against fresh dev; merge only with explicit owner instruction.
- [ ] Atomic job/original-run/window/generation reference binding, rejecting
      payload/foreign references and preserving original run across midnight.
- [ ] Explicit force/legacy policy and earlier-generation receipt evidence;
      no guessed success/unsent or blind replay of uncertain acceptance.
- [ ] Coordinate summary generation/cost with original-run lookup. A rejected
      factory must not erase already incurred usage; no delivery-only LLM rerun.
- [ ] Coordinated builder activation: all publishers use run lock, reconstructed
      frozen parts, current binding checks, single-part transport, immediate
      receipts and caller pacing/backoff together.
- [ ] Truthful partial/blocked/uncertain job/run/UI outcomes and guarded recovery.
- [ ] End-to-end two-target/part/force/legacy/window/restart integration tests;
      full isolated suite at activation checkpoint.

Read current implementation status, this handoff and the prior merged components.
Do not rebuild foundation helpers or confuse opt-in factory tests with activated
business-grade retry. Production migration/deployment is a separate operator step.
