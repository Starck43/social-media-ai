# Integrated roadmap: business readiness before feature expansion

## Authority and status

Revised on 2026-10-08 against dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`; revalidated before merge against dev `fa5fb1b224527865dd830e9024f37a67b1ea4748` ([delta recheck](design/business_readiness_recheck_2026_10_08.md)). **Proposed sequence; this documentation delivery does not implement application changes.** The original roadmap is preserved in [archive](design/archive/roadmap_integrated_legacy.md); competitor research remains historical input, not a current backlog.

Read in this order:
1. [Proposal review](design/proposal_review.md): 40 dispositions, implementation evidence, stale assumptions and verification limits.
2. [Business production readiness](BUSINESS_PRODUCTION_READINESS.md): blockers and release acceptance gates.
3. [Local experience plan](LOCAL_EXPERIENCE_PLAN.md): bounded improvements on the existing architecture.
4. [Future scale strategy](design/future_scale_strategy.md): optional investments and objective entry criteria.

CA-01–04 are substantially present: strict local analysis validation, default/custom text framing, non-DEBUG audit snapshots and model strategy/default resolution. The subsequent dev commit also delivers individual-analysis navigation, conservative metric availability and saved original URLs; these are removed from the remaining UX scope. Keep regression coverage and close remaining boundary gaps rather than reimplement them. Repository migration graph head is 0086; this says nothing about deployed DB revision. Historical test counts are not today's green gate.

## Recommended delivery order

| Stage | Packages | Why this order | Exit evidence |
|---|---|---|---|
| A — Rebase the decisions | Recheck fresh dev; agree first business workflow/capability limits; confirm PRD findings still exist. | User is actively changing dev; baseline findings can become stale. | Exact SHA, bounded scope per PR, no duplicate work. |
| B — Close safety/economics blockers | PRD-01 delivery, PRD-02 rights + UX-02 action registry, PRD-03 spend design; keep publication disabled. | Prevent tenant leakage/global mutations and uncontrolled expense before attracting users. | Cross-tenant/role/registry/concurrency regression tests; approved budget model. |
| C — Make operation repeatable | PRD-04 deploy/restore, PRD-05 truthful outcomes/leases, PRD-06 retention/privacy, PRD-07 AI write boundaries. | A feature-rich bot is not a supported service without recovery and data lifecycle. | Tested production profile, failure/recovery tests, legal/data policy and release packet. |
| D — Reach first trustworthy value | UX-01 onboarding, UX-04 language, read-only UX-05 audit, UX-03 report clarity; PRD-08 visibility. | Reuse current web/agent capabilities; reduce operator-dependent setup. | First-value/recovery journeys, representative latency and usefulness results. |
| E — Managed business pilot | Bounded invited tenants; PRD-09 capability contract and safe-action restrictions; test upgrades and real delivery. | Validate supported workflows, costs and support before self-service. | Signed release gates, restore drill, incident process, pilot feedback and economics. |
| F — Measured optimization/product work | UX-06 spend alerts, UX-07 advice/calibration, UX-08 memory controls, UX-09 filtering; UX-10 export if demanded. | Complete accounting and pilot data make savings/quality decisions meaningful. | Quality/recall versus cost benchmark, customer acceptance, no new hidden side effects. |
| G — Scale/self-service on demand | FUT-01–09 selected by their entry criteria. | Embeddings, billing, tracing and infrastructure are investments, not prerequisites for every deployment. | Separate architecture/product decisions and measured outcomes. |

Some read-only UX work can run alongside safety work if files do not overlap, but the release gates cannot be skipped. No arbitrary calendar/effort promise is made before a fresh scope and workload baseline.

## Candidate first implementation PRs (choose next, not executed here)

1. **Tenant-safe destination resolution** (PRD-01): remove implicit cross-tenant env delivery, deduplicate recipients; focused two-tenant tests. Keep durable per-target retry/concurrency work in a follow-up design if needed.
2. **Agent authorization and registry contract** (PRD-02 / UX-02): reproduce/fix action_send binding, explicit rights and separate tenant/global powers; decide legacy identity migration. Do not turn on live posting.
3. **Spend accounting design + regression harness** (PRD-03): specify units, every-call attribution, unknown-price and concurrent reservation semantics. Approve schema requirements before implementing a ledger.
4. **Production profile and health** (PRD-04): API/runtime ownership, one migrator, private DB, readiness, staging deploy/restore evidence.
5. **First-report checklist** (UX-01): reuse existing writes and readiness; confirm supported source/window/scenario/delivery, not merely “some task exists”.

Default recommendation: begin with PR 1 after confirming current dev. These are separate PRs, not one large feature branch.

## Branch and validation policy

- Read dev; implement in a new branch from its latest committed SHA, never push directly to dev. This documentation review lives in ai/docs-business-readiness-review.
- Keep each task thematic; no repo-wide reformat or unrelated cleanup. The user does not need to enumerate all parallel edits.
- Before handing off an implementation PR, fetch dev, inspect overlap, synchronize in the task branch when appropriate, resolve both textual and semantic conflicts and rerun relevant checks. Report the exact tested SHA. If dev advances again, revalidate before merge.
- Schema changes are separate, explicitly approved work with a single migration head, schema-qualified DDL and staging upgrade/rollback evidence. Absence of migrations does not guarantee absence of conflicts.
- Use isolated test data only. State what ran and what did not; current full suite, not historical counts, gates a release.
- Draft PRs are review proposals. Do not merge without the owner's explicit command.

## Preserved constraints and revised assumptions

Keep: deterministic pipeline, PostgreSQL queue, shared-but-isolated workspaces, encrypted secrets, bounded usage, structured outputs, no permanent raw archive by default, human-controlled risky actions.

Revise: “one VPS forever”, “all exports/caches forbidden”, “hosted business always out of scope”, “first four reliability items absent”, “MAX transport means MAX collection”, “all recorded costs constitute a complete daily cap”, and “tier flag means a live feature is ready”. These were either stale observations or product assumptions, not immutable design rules.
