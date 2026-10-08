# Business readiness: pre-merge delta recheck

## Compared snapshots

- Initial reviewed dev: `f11acefba8fbc2f47e3a17f670a97587208f1dd3`.
- Rechecked dev: `fa5fb1b224527865dd830e9024f37a67b1ea4748` on 2026-10-08.
- New dev commit: **Improve analysis detail navigation and truthful metric availability** (one commit, 24 files, 882 insertions / 305 deletions).
- Documentation PR: #2, `ai/docs-business-readiness-review` into `dev`. Fresh dev was merged into the documentation branch before these status corrections. No application code from this documentation task replaces the user's work.

These are snapshot observations, not a promise that dev will stop changing. Read the latest branch again before each implementation task.

## Newly delivered work — do not repeat

| Area | Inspected implementation / documentation | Backlog consequence |
|---|---|---|
| Individual-analysis navigation | [analytics routes](../../app/web/analytics.py), analysis/group/chain/source/dashboard templates; allowed local origins, contextual back links, direct historic fallback and scoped chain continuation. | UX-03 preserves this; do not build another navigation rewrite. |
| Truthful metric states | [shared renderer](../../app/services/ai/analysis_render.py), analyzer and platform/ingest normalizers distinguish measured zero from missing/partial/unverified data. Sentiment out of 1; author counts are not audience counts; material-level averages replace misleading detail ER. | The individual-analysis portion of UX-03 is delivered. Aggregate/digest freshness, coverage and grouping guidance remain separate. |
| Original publication evidence | Collector/analyzer retain up to 50 distinct safe saved HTTP(S) originals, metric coverage and authors through staged/deferred replay; typed JSON binds preserve metadata. | UX-05 must reuse these links rather than invent another originals feature. Methodology/model audit viewer and budgeted, eligible reanalysis are still proposed. |
| Historical compatibility | Legacy totals/stored engagement_rate remain; unknown historic zeros are conservatively hidden. No automatic reanalysis, historical data rewrite or migration. | Do not describe existing historic records as complete, replayable raw archives or newly backfilled evidence. |
| Tests/reference docs | New metric-availability, render, staging, period and navigation regressions; updated ANALYTICS_AGGREGATION_SYSTEM.md and [implementation review](analysis_detail_review.md). | The implementation review reports 974 passed, 1 skipped, 10 warnings. This is that implementation's recorded run, **not a full-suite execution by this documentation task**. |

## Readiness risks revalidated

Compared AST definitions between the two snapshots for 15 relevant functions: broadcast_digest; build_and_publish/_summarize; has_permission/has_permission_by_codename; daily_cost_today/tenant_daily_cost_limit; analyzer _save_analysis/_build_request_snapshot; execute_job; handle_prune; reap_stale; action_send/_auto_actions_forced_dry_run; health_check. All 15 are structurally unchanged. The action registration/decorator and global-model tools are also unchanged at file level.

Therefore the existing evidence still supports open work on:
- PRD-01: tenant-scoped destination policy and per-target retry/concurrent-send safety;
- PRD-02 / UX-02: interactive identity/global administration separation, explicit action rights and incorrect action_send registry binding;
- PRD-03: complete every-call spend accounting, units and concurrent budget admission;
- PRD-04: API-only Compose versus required runtime ownership, readiness and recovery;
- PRD-05/06: honest handler outcome reporting, stale leases and complete retention policy;
- PRD-07–09: remaining AI write boundaries, operational proof and safe publication/capability promises.

The new analyzer additions concern metric provenance and originals, not the cost meter. Social normalizer changes improve data truthfulness but do not add a MAX collection client. A recorded larger test suite does not itself close production gates.

## Document changes from this recheck

[Proposal review](proposal_review.md), [local experience plan](../LOCAL_EXPERIENCE_PLAN.md), [production readiness](../BUSINESS_PRODUCTION_READINESS.md), [future strategy](future_scale_strategy.md), [roadmap](../ROADMAP_INTEGRATED.md) and [index](../DOCS_INDEX.md) now point to this recheck. Completed individual-analysis work is explicitly excluded from proposed implementation scope. The initial review and archived research remain traceable.

## Verification and limits

Performed: history/diff review against fresh dev; read new code/documentation/tests; structural comparison of the 15 risk functions; Markdown local-link/syntax checks and documentation-only diff check before merge. Existing dev code is preserved by merging rather than rewriting files.

Not performed by this task: full pytest, DB/Alembic execution, real provider/platform calls, browser/load tests, restore/deploy or production changes beyond the authorized merge of documentation into dev. Production readiness remains an open evidence-based decision.

## Next recommendation

Start a new small branch from the then-current dev for **PRD-01 tenant-safe digest destinations**. Keep action authorization/registry work and budget-ledger design separate. No migrations or live publication should be enabled by the documentation merge.
