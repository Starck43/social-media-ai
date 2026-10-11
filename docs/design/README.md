# Development: priorities and task ownership

## One reading path

1. [PRODUCT_PLAN](../PRODUCT_PLAN.md): product outcomes and expansion.
2. [ROADMAP_INTEGRATED](../ROADMAP_INTEGRATED.md): engineering stages A–G and dependencies.
3. This board: the only numbered open-work queue and current reservations.

[Business readiness](../BUSINESS_PRODUCTION_READINESS.md) owns the detailed PRD acceptance contracts. [Implementation ledger](../IMPLEMENTATION_STATUS.md) owns exact merged revisions and original evidence. Fresh GitHub heads/PRs establish actual integration and ownership; neither an archive nor a merged helper closes a release gate.

## Current checkpoint and counting rule

Verified integration checkpoint: dev `88872a27313f79403b69f47ec0fef3e3af461d74`, refreshed 2026-10-11; #85 plan, #86 accounting design and #87 claim-renewal foundation merged. User prioritizes the existing social content collection/analysis path for this bounded package; the nine gate ranks are unchanged. Refresh before selecting code. Foundation remains OPEN; stages B/C are not fully accepted.

**9 major readiness directions remain OPEN:** 7 technical blocker areas (PRD-01–07), 1 managed-pilot area (PRD-08), and 1 safe-publication/release area (PRD-09). Count each stable PRD area once, not its PRs, log sinks, tests, historical findings or evidence requests. This is NOT “nine commits left”, a full backlog-size estimate, or permission to implement every row. Research/Knowledge/Communication/Follow-up/Cases/Approval/Scale are gated product phases, not added to this Foundation count.

## Prioritized open work

The numbers below are descending execution attention/impact, not historical PRD numbering. Keep PRD IDs stable; do not silently renumber completed entries. Dependencies and disabled-feature safeguards override rank. Select one bounded contract before code, and record ownership below; do not turn the order into a release guarantee.

| Priority | Stable gate / important outcome | Remaining bounded work and first action | Exit condition / dependencies |
| --- | --- | --- | --- |
| 1 | PRD-05 — Queue recovery and truthful execution | Approved bounded fail-closed recovery + atomic scheduled/manual admission has owner-reported direct 12/12 EXIT0 on `cdd7130a`; integration via [#93](https://github.com/Starck43/social-media-ai/pull/93), [current contract](general_queue_claim_contract.md#current-bounded-recovery-and-admission-contract). Preserve this slice and merged #87/#89/#91; select only the next approved remaining contract, not repeat completed checks. | Nine-gate rank unchanged. Healthy production timing, noncooperative workers, committed renewal/reaper ordering and external-effect idempotency remain OPEN; this scoped package does not close the whole gate. |
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
| Priority 5 Telegram L1/L2 durable admission (user-selected package 3) | Content coordinator, `fix/telegram-durable-admission`; ingest/poll ACK, shared staging/receipt, collector/TG cursor + NEW checks/contract | PREPARED for Draft on `c0e59dac`; 28 NEW source/task/SQL/session-double + 4 NEW actual offline SQLAlchemy/Telethon checks OK on `adce4fa8` + exact eight-production-file artifact; no driver execution | [Bounded contract](telegram_durable_admission_contract.md). NEW 10 owner PostgreSQL cases prepared NOT RUN; Separate user L1 tagged-fixture admission COMMIT/scoped cleanup approval relayed by chat 2; runner NOT RUN, one sequential pinned local handoff after publication/readback. No schema/bootstrap/provider/sender/backfill or full L1/L2 acceptance; #100 remains independent Draft with deferred tests. |
| Priority 1 claim-fenced heartbeat foundation | Queue coordinator, [#87](https://github.com/Starck43/social-media-ai/pull/87), `fix/job-claim-heartbeat` | MERGED as `9f58f2f6`; author 15 isolated checks on `e7fb385` + patch; owner-reported 9/9 PostgreSQL checks on `f478544e` | Preserve scoped evidence and delivered primitive; no rerun for documentation-only evidence. No dispatcher timer or stale-replay change; healthy-job/external-effect recovery still OPEN. |
| Priority 1 ordinary pre-dispatch ownership gate | Queue coordinator, [#89](https://github.com/Starck43/social-media-ai/pull/89), `fix/job-dispatch-claim-preflight` | MERGED as `cdc82b9f`; 16 new source/async-double checks OK on `9f58f2f` + patch; baseline 2 failures/12 errors | Preserve preflight; periodic ordinary supervision is a separate queue-coordinator lane. Reject lost/unconfirmed renewal before handler/retry/outcome. No timer or healthy-long-job/reaper race acceptance; no local task. |
| Priority 1 ordinary periodic heartbeat supervision | Queue coordinator, [#91](https://github.com/Starck43/social-media-ai/pull/91), `fix/job-ordinary-heartbeat` | MERGED as `8954512b`; author 22 isolated task checks on `cdc82b9f` + patch; owner-reported 8/8 PostgreSQL checks on `4a3eacd3` | Preserve tested source/runner and original evidence; no rerun. Remaining queue acceptance needs a separate bounded audit. Cooperative stop/drain delivered in this slice; healthy production timing, independent-connection races and external-effect replay still OPEN. |
| Priority 1 fail-closed recovery and atomic trigger admission | Queue coordinator (chat 2), [#93 integration record](https://github.com/Starck43/social-media-ai/pull/93), `fix/queue-recovery-admission`; existing ordinary queue/managers/scheduler/UI/prune + scoped tests/contract/board/ledger | MERGED as `88872a27313f79403b69f47ec0fef3e3af461d74`; final head `24f22d92`; owner direct as-is 12/12 EXIT0 on `cdd7130a`, exact runner `ea817635…`; author 39 NEW isolated checks. Actual merge independently fetched; original execution attribution is unchanged | No local task or retest pending. Final closeout is three docs only, all app/test bytes unchanged. Reservation released after verified #93 integration. Earlier `1743a296` shim evidence remains separate; production timer/noncooperative kill/effect idempotency still OPEN. |
| Priority 2 every-call accounting/admission contract | Accounting coordinator, [#86](https://github.com/Starck43/social-media-ai/pull/86), merged as `79c4b2c` | MERGED DESIGN ONLY; PRD-03 policy/schema approval OPEN | Owner chooses pricing/cap/uncertainty/ledger decisions before format/schema or runtime wiring; no local DB task. |
| Priority 5 submitted-text sampling coverage | Content coordinator, `fix/text-sampling-coverage`; classifier/analyzer + new isolated test + existing board/ledger | MERGED #88 as `d933f1a0`; 16 new isolated checks on `79c4b2c` + patch, baseline 8 failures/1 missing-selector error | Preserve legacy prompt sample/cap; store only eligible submitted-text hashes, keep omitted posts staged and mark incomplete coverage. Genuine #76 partial guard/media-only/cancellation retained. Actual merge verified; original readback/GitGuardian/tree evidence remains content-coordinator-reported. No historical hash repair or DB/local-owner task; reservation released. |
| Priority 5 silent image/video coverage gaps | Content coordinator, `fix/media-skip-coverage`; analyzer base/images/videos/save + one shared pure gap helper + new isolated test + board/ledger | MERGED #90 as `0b4781c8`; 22 new isolated checks on `cdc82b9f` + patch, baseline 17 failures | Missing model/URLs or empty parsed media output cannot certify parent-post hashes; useful siblings persist with incomplete coverage. Preserve text sampling/#76/counters/cancellation and per-item hash retirement. No schema/retry/billing/provider/DB/local task; actual merge fetched; content-coordinator source/readback/check/tree evidence stays separately attributed. Historical hashes and attachment staging fidelity remain OPEN; reservation released. |
| Priority 5 durable staged attachments contract | Content coordinator, [#92](https://github.com/Starck43/social-media-ai/pull/92), `feat/staged-attachments-contract`; snapshot utility/model/storage/collector/replay + migration 0089 + new checks | MERGED as `359eace2`; final head `63ed87f1`; original author 18-check preparation retained in ledger; owner-local 0089/alembic check + four driver checks reported in continuation handoff | Preserve NULL unknown/[] none and conservative references. Actual merge fetched; owner-local execution remains separately reported, not rerun here or queue evidence. No backfill/download/provider/full PRD-07 acceptance. Direct VK normalization merged separately as #94; its original checks remain independently attributed. |
| Priority 5 direct VK photo snapshots | Content coordinator, [#94](https://github.com/Starck43/social-media-ai/pull/94), `fix/vk-direct-photo-snapshots`; VKClient + one new isolated test only | MERGED as `84524c5a`; head `5b13510c`; owner lane released | Original author 15 checks on `359eace2` + source artifact remain separately attributed; largest valid pixel area/first ties, invalid URLs as placeholders. No signed-query/video resolution/download/DB/provider/full-media acceptance. Shared queue docs unchanged by #94. |
| Priority 3 tenant-scoped agent session uniqueness | Owner-submitted [#95](https://github.com/Starck43/social-media-ai/pull/95), `fix/agent-session-tenant-scope`; session model/manager + migration FILE 0090 + docs/tests/runtime docstrings | OPEN; no #93 file overlap, combined merge-tree clean; owner PR-body 65 targeted passes not rerun | Preserve its source/ownership and review schema/deployment provenance separately. No new DDL/migration/DB task authorized here. Unpublished LLM override/runtime work remains owner-occupied; do not overwrite it. |
| Priority 5 Telegram L2 media placeholders | Content coordinator, `fix/telegram-media-placeholders`; TGClient two normalization paths/pure helper + new isolated test + existing board/ledger | SOURCE-VERIFIED; 17 NEW isolated checks on `88872a2` + source artifact; publication/integration checked separately | Retain recognized media-only photos/videos; captions with unavailable media cannot certify parent hashes. No URL resolution/download/Telethon/provider/schema/cursor change. L1 ingest caption/watermark/staging contract, unsupported documents, historical repair and complete media acceptance remain OPEN; no local DB task. |
| Model-only default and capability-aware chat routing | Owner-submitted [#96](https://github.com/Starck43/social-media-ai/pull/96), `fix/llm-model-default-flag`, head `1be1dcc1`; stacked on #95; LLM/provider/API/admin/runtime + migration FILE 0091 + docs/tests | OPEN; preserve ownership; #95 precedes #96, 0091 depends on 0090 | Owner PR-body reports 199 targeted passes/manual DDL, not rerun or independently executed by content coordinator. Resolve revision/manual-DDL provenance separately; DROP/rebuild of shared test_schema suggested in PR body is NOT authorized by this lane and must not be included in testing handoffs. No schema/runtime edits or deployment assigned here. |

All implementation reservations from #84 and earlier packages are released at this checkpoint. A future lane can be occupied without a PR; coordinate before editing shared code/docs. The priority-1 owner-only PostgreSQL task returned 9/9 on the pinned source head; no repeat of that task, old tests or #74 checks. The NEW periodic-supervision owner task returned 8/8 on its pinned source head; no pending local task or repeated checks. Finished checks are not rerun without a specific changed risk.

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
