# Development: priorities and task ownership

## One reading path

1. [PRODUCT_PLAN](../PRODUCT_PLAN.md): product outcomes and expansion.
2. [ROADMAP_INTEGRATED](../ROADMAP_INTEGRATED.md): engineering stages A–G and dependencies.
3. This board: the only numbered open-work queue and current reservations.

[Business readiness](../BUSINESS_PRODUCTION_READINESS.md) owns the detailed PRD acceptance contracts. [Implementation ledger](../IMPLEMENTATION_STATUS.md) owns exact merged revisions and original evidence. Fresh GitHub heads/PRs establish actual integration and ownership; neither an archive nor a merged helper closes a release gate.

## Current checkpoint and counting rule

Planning checkpoint: dev `79c4b2c8d669e0a99af16e8b118355f48b20a376`, 2026-10-10; #85 plan and #86 accounting-design revision merged, no open PRs before this queue package. Refresh before selecting code. Foundation remains OPEN; stages B/C are not fully accepted.

**9 major readiness directions remain OPEN:** 7 technical blocker areas (PRD-01–07), 1 managed-pilot area (PRD-08), and 1 safe-publication/release area (PRD-09). Count each stable PRD area once, not its PRs, log sinks, tests, historical findings or evidence requests. This is NOT “nine commits left”, a full backlog-size estimate, or permission to implement every row. Research/Knowledge/Communication/Follow-up/Cases/Approval/Scale are gated product phases, not added to this Foundation count.

## Prioritized open work

The numbers below are descending execution attention/impact, not historical PRD numbering. Keep PRD IDs stable; do not silently renumber completed entries. Dependencies and disabled-feature safeguards override rank. Select one bounded contract before code, and record ownership below; do not turn the order into a release guarantee.

| Priority | Stable gate / important outcome | Remaining bounded work and first action | Exit condition / dependencies |
| --- | --- | --- | --- |
| 1 | PRD-05 — Queue recovery and truthful execution | Pin the normal-job long-running/recovery contract: review stale reaping versus a healthy worker, lease/timeout ownership, concurrent schedule ticks, provider backoff and quarantine visibility. Preserve merged ordinary claim fencing, Job/task atomicity and attempt budgets; do not rebuild them. | Fresh scoped failure/concurrency evidence shows healthy jobs are not duplicated, abandoned work has a bounded resolution, and per-handler outcomes remain truthful. External effects need their own idempotency. First code package follows source review, not an automatic lease/schema rewrite. |
| 2 | PRD-03 — Every-call accounting and bounded spend | After the queue contract, design one usage/admission contract for analysis/chat/digest/learn, invalid/filtered/failed-save/fallback attempts, USD versus cents and unknown tariffs. Partial daily-row collisions currently lose B usage/history; mutable analytics/rollups are not a paid-attempt ledger. | Approved concurrent cap/overshoot semantics and durable per-attempt accounting; unknown != free. Accounting format/reservation schema requires explicit approval before implementation. No billing migration by default. |
| 3 | PRD-02 — Identity, permissions and durable approvals | Complete the remaining actor/tenant/tool/write matrix, revoke/replay/TTL and simultaneous-confirmation contract. Verify global administration and credential boundaries; retain delivered fail-closed active-user/session/registry safeguards. | Unauthorized/unknown/revoked identities cannot read/write foreign/global data or replay approvals. Membership migration and approval-state/schema changes are separately scoped/approved. |
| 4 | PRD-01 — Retry-safe tenant delivery | Accept merged per-target/per-part checkpoints under partial failures, lost acknowledgements, concurrency and lease loss. Define conservative operator recovery without resetting receipts or enabling force/live send automatically. | Correct tenant recipients, failure-only retries, visible no-channel outcomes and ambiguous-send stops; staging/transport evidence and separate live authorization. Not an exactly-once claim. |
| 5 | PRD-07 — Safe AI output and evidence coverage | Close scenario-schema support/save-time validation and poisoned-input/quality/evidence contracts. Preserve typed outputs and atomic memory batches already merged. Pin partial-result/digest coverage and cancellation/failed-output behavior; coordinate every-call costs with priority 2. | Invalid/untrusted output cannot gain rights or write arbitrary state; saved hashes/content coverage and factual-quality limits are explicit. Existing #76 partial-row safeguard is not complete coverage/accounting. No speculative storage-format expansion. |
| 6 | PRD-06 — Data lifecycle, privacy and offboarding | Decide data-class retention/backups/deletion replay; remaining credential revocation, offboarding/export, bounded tenant policies and log/tracing privacy. Existing merged log/tenant fixes are retained, not restarted. | Approved policy plus scoped deletion/revocation evidence; no cross-tenant damage or loss of unresolved effect/accounting evidence. Periods remain UNDECIDED; no purge/schema/live action from this plan. |
| 7 | PRD-04 — Reproducible deployment and restore | Review a production profile with one migrator/scheduler/listener owner, private DB, secure edge, loop freshness/drain and encrypted DB+vault-key backup. Preserve merged readiness/secure-cookie behavior. | Separately authorized staging install/upgrade/rollback/restore evidence; restored tenant can use its keys. No deployment, migration or restore run is currently assigned. |
| 8 | PRD-08 — Observability, first value and support | Reuse existing outcomes/history for source freshness, backlog/quarantine, stalled-loop, delivery/spend/unknown-price signals; name support/escalation ownership and a bounded read-only first-value journey. | Actionable deduplicated redacted alerts, agreed pilot usefulness/latency/freshness measurements and support ownership. External tracing requires a data-transfer decision, not a default integration. |
| 9 | PRD-09 — Safe action activation and honest release promises | Keep external publication disabled/preview-only; define actor-bound versioned approval, fresh rights/credentials, claim/idempotency, audit and kill switch before live actions. Publish only accepted capabilities; MAX transport is not MAX collection. | Explicit release/capability sign-off after applicable priorities 1–8; live activation is separately approved. Invited read-only pilot restrictions do not authorize unattended writes. |

## Current ownership and next package

| Work | Owner / branch | State | Next action |
| --- | --- | --- | --- |
| Numbered plan and evidence-preserving navigation cleanup | #85, merged as `e7fb385` | MERGED documentation-only; nine major gates still OPEN | Preserve count/links/evidence; do not reopen finished planning cleanup. |
| Priority 1 claim-fenced heartbeat foundation | Queue coordinator, [#87](https://github.com/Starck43/social-media-ai/pull/87), `fix/job-claim-heartbeat` | READY for scoped integration; author 15 isolated checks on `e7fb385` + patch; owner-reported 9/9 PostgreSQL checks on `f478544e` | Fresh final-head/check/readback integration; no rerun for documentation-only evidence. No dispatcher timer or stale-replay change; healthy-job/external-effect recovery still OPEN. |
| Priority 2 every-call accounting/admission contract | Accounting coordinator, [#86](https://github.com/Starck43/social-media-ai/pull/86), merged as `79c4b2c` | MERGED DESIGN ONLY; PRD-03 policy/schema approval OPEN | Owner chooses pricing/cap/uncertainty/ledger decisions before format/schema or runtime wiring; no local DB task. |
| Priority 5 submitted-text sampling coverage | Content coordinator; content_classifier.py text selection + analyzer.py text/save coverage boundary + new isolated test | RESERVED; source-grounded contract narrowing, no execution evidence claimed | Do not cover/retire unsent texts; preserve prompt cap/media/cancellation/#76 partial-row collision guard. No schema/billing/queue or shared docs overlap. |

All implementation reservations from #84 and earlier packages are released at this checkpoint. A future lane can be occupied without a PR; coordinate before editing shared code/docs. The priority-1 owner-only PostgreSQL task returned 9/9 on the pinned source head; no pending local task, old tests or #74 reruns. Finished checks are not rerun without a specific changed risk.

## Delivered work is evidence, not the open queue

Merged bounded work through #84 is recorded in the [ledger](../IMPLEMENTATION_STATUS.md), with original SHA/patch/owner attribution. Queue/identity/typed-memory/digest safeguards and log privacy fixes remain delivered; they are not nine new implementation assignments. #74 six-check owner evidence and statically reconciled artifact identity remain separate from older 11-check evidence, #81 fixture evidence, #77/#80 SQL patches and current-dev acceptance.

The previous long board and stale next-session continuation are preserved in [one historical snapshot](archive/task_board_pre_priority_2026_10_10.md). They must not override this queue. Referenced contracts, source reviews and test evidence are retained; completion alone is not a reason to delete them.

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

## Maintenance rule

Change this numbered queue only when scope/priority/gate evidence changes. Update the existing ownership row for the one selected package; keep transient commands/results in its commit/PR and exact historical evidence in the ledger. Closed gate means acceptance/sign-off, not merely MERGED. Maintain links when archiving; do not create new competing handoff/backlog files. Routine bounded Ready/merge uses standing authorization with fresh heads/required checks; conflicts/red checks/unclear scope/elevated risk require clarification. No direct dev push, schema/reset/migration/live/deploy or check weakening.
