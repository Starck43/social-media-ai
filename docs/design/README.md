# Development: current stage and task ownership

## Read only these three levels first

1. **Global plan:** [PRODUCT_PLAN](../PRODUCT_PLAN.md) — product outcomes and expansion.
2. **Stages:** [ROADMAP_INTEGRATED](../ROADMAP_INTEGRATED.md) — existing A–G sequence and exit gates.
3. **Current-stage tasks:** this page — what is occupied, done, available or gated.

[Implementation ledger](../IMPLEMENTATION_STATUS.md) records integrated changes;
GitHub dev/open PR heads establish current merge/ownership state. Individual
handoffs explain technical decisions/evidence, not a second global backlog.
[Release gates](../BUSINESS_PRODUCTION_READINESS.md) remain binding.

## Current stage: Foundation, safety and repeatable operation

Current source baseline: dev `50dc2a2b0bb9832c05b426ea16c6010616264b31` is the reconciliation snapshot for this branch (carried in by merge `ef4fd13`), not a claim of permanent currency; dev may have moved again and this docs branch does not chase every movement. At the time of the queue merges the THEN-dev head was `073fcd0` (PR #43). Dev advanced by four owner-approved merges: [PR #45](https://github.com/Starck43/social-media-ai/pull/45) queue foundation integration as `f078f3a1d5a57985d31e6a7a7380bf5bc98f9b3e` (closed #34/#35/#37), [PR #46](https://github.com/Starck43/social-media-ai/pull/46) as `8f3c6127d835b615d624f5e461dc6697c930fa0a`, [PR #49](https://github.com/Starck43/social-media-ai/pull/49) as `7ad1d75b263df953ef8163d2ac990a9c6cc4589f`, and [PR #43](https://github.com/Starck43/social-media-ai/pull/43) as `073fcd0`. The parallel refactoring lanes then merged: #30 tenancy-model layout as `50dc2a2`, #48 stored-analysis lookup as `2aa0b43`, #42 date parsing as `137dd1e`, #47 direct CLI awaitables as `3551f26`, #44 CLI username parsing as `c765365`, PR31 notification-model layout as `f333403`, PR32 collection-model layout as `ef0de1c`. Each merge's checks stay attached to its own tested SHA below; no historical full suite is re-attributed to this baseline.

Integrated baseline dev `683c49e85918503119af47338200d8f762a52403`: PR22 merged
by explicit Owner approval, history preserved. Merge tree equals approved
56d8105; executable code equals Owner-green 482eacb 1612 passed/11
warnings/64 subtests/saved exit0. That full suite belongs to PR22 head 482eacb
and merged 683c49e only; it is not carried onto dev 073fcd0, and no full-suite
run exists for the #43/#45/#46/#49 merges — their evidence is the targeted
checks recorded below at their own tested SHAs. Merged/regression-verified does
not establish deployment, live activation, or business/security acceptance. The
general-queue foundation is now merged (current baseline); remaining queue
gates stay OPEN and broader gates remain OPEN.
Product **Foundation** is still open; engineering stages **B/C** have unfinished
gates. This is not a declaration that all earlier/later stage gates passed.
Do not restart merged digest integration or bounded logging work.

| Stage / task | State and allocation | What to do next |
| --- | --- | --- |
| B / identity and permissions, PRD-02 | **MERGED bounded safeguards:** [PR #22](https://github.com/Starck43/social-media-ai/pull/22) as 683c49e; Owner 482eacb full 1612 passed/11 warnings/64 subtests/saved exit0 | Do not restart delivered boundary/tests. Broad XSS/durable approval/CAS/browser/live/release acceptance OPEN. [Handoff](identity_permissions_handoff.md); PR29/30/31/32 separate. |
| C / general queue, PRD-05 | **MERGED bounded foundation:** [PR #45](https://github.com/Starck43/social-media-ai/pull/45) as `f078f3a` carrying closed #34 (atomic stale reaping), #35 (docs wording) and #37 (outcome contract docs); [PR #46](https://github.com/Starck43/social-media-ai/pull/46) dispatcher outcome-error boundary as `8f3c612`; [PR #49](https://github.com/Starck43/social-media-ai/pull/49) truthful web job outcome as `7ad1d75`; [PR #51](https://github.com/Starck43/social-media-ai/pull/51) ordinary claim fencing as `351f3b6` | Code evidence at tested SHA `81138d99dab987b3355e674d5bf2922df59e7d49`: standalone 5 OK/exit 0 (log `pr34-rerun-standalone-20261010-054125.txt`), config check exit 0 (log `pr34-rerun-schemacheck-20261010-054130.txt`), PostgreSQL 12 passed/exit 0 (log `pr34-rerun-dbtest-20261010-054136.txt`). Byte identity applies ONLY to `app/models/managers/job_manager.py`, `tests/test_job_stale_reap.py` and `tests/test_job_stale_reap_db.py` between the tested SHA and integration head `6decbf9` (docs-only delta `e9d25d3`, other files not claimed). #46 at tested `6979feb` (25+13+14/exit 0); #49 at tested `74af779` (13/exit 0); #51 at tested `dbc054c` (77 standalone + 15 PostgreSQL, exit 0). Full suite NOT run at merged heads. Bounded ordinary Job/task atomicity is merged in #53 (`24386628`; owner-tested `e3e2226`, 27+15+7/exit 0); #52 web feedback is merged (`10d16090`, final `4f664786`, owner-tested `f613839`, 15/exit 0). Operator cancellation #54 is MERGED (`ee43592a`, final `7316a084`, owner-tested `4d65af4`: 14+15 standalone, config check, 10 PostgreSQL; all exit 0). Cleanup_done is OCCUPIED/PREPARED on `fix/atomic-completed-job-cleanup`, checks not run; web deletion is assigned separately to the local helper. General leases, recovery, ordering, spend and broader fencing remain OPEN. |
| B / per-attempt accounting and reservation design, PRD-03 | Owner sequence places design after queue; schema approval required | Do not duplicate that lane or implement a billing migration from this table. |
| C / bounded log privacy, PRD-06 | **MERGED:** dispatcher #20, retirement #23, attempt-count #25; #25 merge `eb49d1d` verified | No repeat implementation or automatic test rerun. Remaining collect/analyze/prune/provider/ORM logs are not globally sanitized. |
| C / typed boundaries and memory, PRD-07 | Typed boundaries #17 merged; **atomic learn memory batch MERGED:** [PR #43](https://github.com/Starck43/social-media-ai/pull/43) as `073fcd0`, head/tested `b29e432` (49 boundary + 11 manager standalone + 11 PostgreSQL, exit 0) | Do not rebuild typed output contracts or the atomic batch. Acknowledgement-loss rollback proof, ordinary claim fencing, duplicate LLM spend, fact quality and task/billing atomicity remain OPEN. |
| A/C / development documentation | **MERGED:** [PR #26](https://github.com/Starck43/social-media-ai/pull/26) as `10212b5e`; no application change | Use the three-level entry point; do not redo this navigation cleanup. |
| C / observation and recovery runbook, PRD-04/05 | **MERGED:** [PR #27](https://github.com/Starck43/social-media-ai/pull/27) as `0234c21`; guide `b1cf91f` | [Existing DEPLOYMENT runbook](../DEPLOYMENT.md#observation-and-conservative-recovery): source-grounded observation/escalation, no automatic reset/replay/restart. Eight tabletop cases prepared, NOT run. |
| D–G / UX, pilot, expansion and scale | Gated, not current automatic implementation scope | Use stage exit evidence and an explicit selected user journey, not old research checklists. |

The owner reports having run checks and pushed changes. Do not ask for or execute
the same checks again by default. Detailed evidence belongs to the submitted
revision/PR and owner logs: no full-suite count or tested SHA is invented here.
Latest Owner completed evidence: exact 482eacb 1612 passed/11 warnings/64
subtests/738.25s/saved exit0, unchanged HEAD. Approved merge 683c49e has
identical executable code; post-merge journal diff is docs-only, no retest by
default. Separately approved one-row TEST cleanup (zero dependencies) is not
future cleanup/reset permission. GitGuardian success; no invented independent
tests/CI/warning triage.
Merged, owner-reported checks, independently observed checks, deployed and
accepted remain different states.

### Concurrent-work boundary

PR30 (tenancy-model split) is **MERGED** as `50dc2a2b0bb9832c05b426ea16c6010616264b31`,
PR31 (notification-model layout) as `f333403208d266fdb1978df03afa9d6277010558` and
PR32 (collection-model layout) as `ef0de1c1a34ed74d546f87af70fe790aee9db825`
(git ancestry verified on dev); none of those lanes is an open assignment here.
PR29 remains deferred. The compatibility/date/cli lookup lanes #42 (`137dd1e`),
#44 (`c765365`), #47 (`3551f26`, stacked on #44) and #48 (`2aa0b43`) are merged
the same day; see the journal for their tested SHAs.

PR22 is closed/merged; PR33 records its FOUR-file post-merge documentation
status only, not identity/runtime implementation.
The model-layout lanes PR31/PR32 are merged (SHAs above); PR29 remains deferred.
Check fresh heads and file ownership before the next package; preserve parallel
corrections. The compatibility pointer and detailed handoff explain evidence,
not competing backlog. Owner separately approved integration of this docs-only
record; that authorization does not cover PR29 or future code changes.
Queue continuation [PR #51](https://github.com/Starck43/social-media-ai/pull/51)
(ordinary claim fencing) merged at the historical merge checkpoint `351f3b6`
(dev has since advanced); the merge SHA is distinct from the owner-tested
`dbc054c7f37ed1071711afea750e044b7e50ffd6`, owner-reported 77 standalone + 15
PostgreSQL checks exit 0, review published on that PR. Queue atomicity [PR #53](https://github.com/Starck43/social-media-ai/pull/53)
is **MERGED** as `243866281f1bb2e3ed87827601e2705e82b6c3df`; owner-tested
`e3e222673bb39debe2b07cdf4fd7d95b71a8dac1`: 27 unit + 15 existing DB + 7 new DB,
all exit 0. [PR #52](https://github.com/Starck43/social-media-ai/pull/52) is
**MERGED** as `10d160901b7305a0ed22d15fd146b7573d6a399b`, final head
`4f66478668a584ba9e23cb5fa7f40712286b3576`; code/tests equal owner-tested
`f61383936cb3dfaec3c5ff09ab6442a106ee0159` (15 standalone, exit 0), final delta
docs-only. No full suite or combined-head check is claimed. Operator cancellation [PR #54](https://github.com/Starck43/social-media-ai/pull/54)
is **MERGED** as `ee43592a05d706168e7b80ece87ea17e2de34641`, final head
`7316a084231591fbbcfbac207a4ddd7d7841e99e`. Owner-tested
`4d65af4e55c6b3096db2278ce2589cfc17f1525e`: 14 cancellation + 15 web outcome
standalone, config check and 10 PostgreSQL checks, sequential/all exit 0;
[owner evidence](https://github.com/Starck43/social-media-ai/pull/54#issuecomment-6097327068).
Final delta was three docs only; executable files equal the owner-tested revision.
No merged-head/full-suite run, deployment or acceptance is implied.
Current queue lane: **OCCUPIED / PREPARED**, `fix/atomic-completed-job-cleanup`
from dev `ee43592a`; owner checks prepared/not run. Remote scope: JobManager
cleanup_done, separate cleanup tests and existing board/ledger/claim contract.
Independent local helper assignment: `fix/web-job-delete-race`, only web jobs
job_delete + tests/test_web_job_delete_race.py; awaiting owner-forwarded start.
The parallel agent retains runtime/session/message helpers; none are changed here.
Recovery / ordering / spend and broader fencing remain **OPEN**; PR29 **deferred**.

### Latest verified merge checkpoint

Fresh source checkpoint: dev `ee43592a05d706168e7b80ece87ea17e2de34641`.
Model-layout group is **MERGED**: #36 analysis (`f2f53b1`), #38 identity
(`7d8c731`), #39 agent (`c1d3b9e`), #41 scheduling (`b4d48c6`); earlier
#30/#31/#32 layout merges remain recorded above. Runtime whitespace #40 also
merged as `bcff693`. Git ancestry establishes integration, not new test evidence.
Their checks remain attached to original owner-tested revisions; no new full
suite, deployment or acceptance is claimed by this reconciliation.

## Technical catalog — open only what the selected task needs

Every existing top-level design document is assigned below. A category does not
certify implementation or acceptance. Older documents retain their original
baseline; do not execute their historical "next" section as a new assignment.

### Delivery contracts and acceptance evidence

- [Retry design](digest_delivery_retry_plan.md), [schema review](digest_delivery_state_schema_review.md).
- [Checkpoint contract](digest_checkpoint_contract_handoff.md), [store](digest_checkpoint_store_handoff.md).
- [Single-part transport](digest_single_part_transport_handoff.md), [HTML parts](digest_html_parts_handoff.md).
- [Atomic snapshot](digest_atomic_snapshot_handoff.md), [job integration](digest_job_delivery_handoff.md).
- [Tenant-safe routing review](tenant_safe_digest_delivery_review.md).

### Runtime, outcomes and privacy evidence

- [Bootstrap/readiness](bootstrap_readiness_handoff.md), [returned failures](returned_job_failures_handoff.md).
- [Typed output boundaries](typed_output_boundaries_handoff.md).
- [Dispatcher privacy](dispatcher_log_privacy_handoff.md), [retirement warning](staged_retirement_log_privacy_handoff.md), [attempt-count warning](staged_attempt_log_privacy_handoff.md).

These contracts are retained at their paths to preserve active PR references;
they are not independent current-task lists. Their current merge state comes
from dev/ledger, not an old PREPARED label.

### Product/architecture proposals and design context

- [Vision](vision.md), [product-direction delivery record](product_direction_handoff.md).
- [Deployment proposal record](deployment_architecture_handoff.md), [scale criteria](future_scale_strategy.md).
- [Personal chat routing proposal](personal_chat_workspace_routing_plan.md) — not shipped or assigned here.
- [UI design](ui.md), [analytics UI proposal](analytics_ui_refactor.md).

### Research and review inputs — not the current backlog

- [Proposal review](proposal_review.md), [readiness delta](business_readiness_recheck_2026_10_08.md), [reliability review](reliability_review_2026_10_08.md).
- [Competitive research](competitive_analysis.md), [analysis experience research](analysis_and_best_experiences.md).
- [Analysis detail](analysis_detail_review.md), [drilldown](analytics_drilldown_review.md), [filters](analytics_filters_review.md), [chain navigation](chain_navigation_review.md).

### Historical navigation/model work

- [Navigation preparation record](archive/documentation_organization_handoff.md).
- [Model reconciliation record](archive/model_reference_reconciliation_handoff.md).
- [Historical archive index](archive/README.md) — preserved evidence, not release authority.

The two old top-level filenames remain compatibility pointers, not active
reports. Other technical contract paths remain stable for active PR references.

## Current package and retained cleanup evidence

PR #26 navigation/archive cleanup is **MERGED** as `10212b5e` on the owner's
integration. Original preparation commits: navigation `185a500`, archive/link
cleanup `bd3d893`, PR-state `35d4144`. All 34 design documents were cataloged;
two original reports were archived byte-identically with compatibility pointers.
Its static link/anchor/fence checks are historical documentation evidence, not
application/deployment acceptance. Do not repeat the cleanup.

Operator package [PR #27](https://github.com/Starck43/social-media-ai/pull/27)
is **MERGED** as `0234c21` on the owner's current instruction. Source baseline
`10212b5e`; guide `b1cf91f`, navigation `24c91f8`, final head `a874ada`.
GitGuardian and Kilo completed successfully for that head; Kilo reported no
issues. Fresh dev verified after integration. The latest pre-merge PR #22 file
comparison found ZERO overlap with these three documentation files; that branch
was not merged or modified. Source-grounded
process/HTTP observations, job/part outcome interpretation, evidence preservation
and separately authorized change gates. Eight new tabletop cases are written,
NOT executed; no probes, service commands, tests, DB or external calls here.
Unsafe legacy incident stamp/direct-send/live-volume-tar suggestions removed;
installation/update examples remain explicitly separate drafts, not a verified
production profile. No new runbook/handoff file.

Static checks: registered root health routes and main include; actual db/api
Compose and auto-migration startup; job-manager defaults; digest static categories;
new source links, anchors/fences/added whitespace and observation-command scope.
These are text/source checks, not full-repo crawling, rendering, staging fault
injection or restore acceptance. PR #22's occupied source/status/identity files
are unchanged. Eight tabletop cases remain prepared/unexecuted; merge does not
certify staging fault handling, backup restore or production acceptance. Next:
operator documentation/tabletop review, then select an unoccupied task using
fresh dev/open PRs; do not duplicate the assigned identity/queue/budget lane.
Previously owner-run application checks are not repeated. New implementation or
actual recovery/migration/activation still requires its own authorization.

## Maintenance rule

One global plan, one stage roadmap, one current-stage board. Update the relevant
row and the integration/evidence ledger, not a new competing backlog. Put local
commands/evidence in the PR and English Owner handoff commit body. Add a separate
technical contract only for durable nonduplicated behavior/decisions. Reuse it
for later small changes. Keep compatibility paths when another PR depends on
them; archive only after preserving source and migrating known links.

This is a documentation-only cleanup, not a feature/security audit. No tests,
DB, migration, provider/messenger call or sender activation is performed here.
