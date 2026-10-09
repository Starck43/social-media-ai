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

Snapshot: dev `0234c21ed2c1f171d46732153d91e654e9b3cfdb`, 2026-10-09, after authorized PR #27 integration.
Product **Foundation** is still open; engineering stages **B/C** have unfinished
gates. This is not a declaration that all earlier/later stage gates passed.
Do not restart merged digest integration or bounded logging work.

| Stage / task | State and allocation | What to do next |
| --- | --- | --- |
| B / identity and permissions, PRD-02 | **OCCUPIED:** draft [PR #22](https://github.com/Starck43/social-media-ai/pull/22), `ai/identity-permissions-boundary`, live head owned by that PR | Linked identity/fresh-confirmation/safe-preview block PREPARED; 46 new methods NOT RUN. Owner verification and remaining coverage/compatibility gates: [handoff](identity_permissions_handoff.md). Do not duplicate it. |
| C / general queue, PRD-05 | Sequenced after identity in the same owner-assigned lane; no separate open PR observed | Coordinate allocation before starting. Checkpoint fencing is not general queue correctness. |
| B / per-attempt accounting and reservation design, PRD-03 | Owner sequence places design after queue; schema approval required | Do not duplicate that lane or implement a billing migration from this table. |
| C / bounded log privacy, PRD-06 | **MERGED:** dispatcher #20, retirement #23, attempt-count #25; #25 merge `eb49d1d` verified | No repeat implementation or automatic test rerun. Remaining collect/analyze/prune/provider/ORM logs are not globally sanitized. |
| C / typed boundaries and memory, PRD-07 | Typed boundaries #17 merged; memory transaction/watermark concurrency remains open | Separate package after allocation check; do not rebuild typed output contracts. |
| A/C / development documentation | **MERGED:** [PR #26](https://github.com/Starck43/social-media-ai/pull/26) as `10212b5e`; no application change | Use the three-level entry point; do not redo this navigation cleanup. |
| C / observation and recovery runbook, PRD-04/05 | **MERGED:** [PR #27](https://github.com/Starck43/social-media-ai/pull/27) as `0234c21`; guide `b1cf91f` | [Existing DEPLOYMENT runbook](../DEPLOYMENT.md#observation-and-conservative-recovery): source-grounded observation/escalation, no automatic reset/replay/restart. Eight tabletop cases prepared, NOT run. |
| D–G / UX, pilot, expansion and scale | Gated, not current automatic implementation scope | Use stage exit evidence and an explicit selected user journey, not old research checklists. |

The owner reports having run checks and pushed changes. Do not ask for or execute
the same checks again by default. Detailed evidence belongs to the submitted
revision/PR and owner logs: no full-suite count or tested SHA is invented here.
PR #22's newer fixture is distinct from its earlier owner-run baseline.
Merged, owner-reported checks, independently observed checks, deployed and
accepted remain different states.

### Concurrent-work boundary

PR #22 owns current edits to `../IMPLEMENTATION_STATUS.md`,
`next_tasks_handoff.md`, TENANCY and its identity source/tests. This documentation
branch does **not** rewrite those files or copy its implementation. GitHub heads
must be rechecked before the next package/merge. The legacy
[next-session notes](next_tasks_handoff.md) stay compatible while that PR is open;
use this page for task selection, the active PR for its detailed continuation.
After synchronization, fold transient notes into this board rather than create
another session-wide handoff. Preserve both lanes when reconciling the ledger.

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
