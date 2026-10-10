# Integrated roadmap: business readiness before feature expansion

## Authority and status

Revised on 2026-10-08 against dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`; revalidated before merge against dev `fa5fb1b224527865dd830e9024f37a67b1ea4748` ([delta recheck](design/business_readiness_recheck_2026_10_08.md)). **Historical planning baseline; current implementation/merge status is tracked in [Implementation status](IMPLEMENTATION_STATUS.md).** The original roadmap is preserved in [archive](design/archive/roadmap_integrated_legacy.md); competitor research remains historical input, not a current backlog.

Navigation refreshed against dev `eb49d1d` on 2026-10-09. This document owns
**engineering stages A–G**, not a second rolling task backlog. Product phases live
in [PRODUCT_PLAN](PRODUCT_PLAN.md); Foundation spans the readiness work here.
For selected current-stage tasks and occupied PRs use [the task board](design/README.md).
For exact merge/evidence history use [Implementation status](IMPLEMENTATION_STATUS.md)
and current GitHub state. Historical baseline paragraphs below retain their date.

Supporting rationale (read only for the chosen task):
1. [Proposal review](design/proposal_review.md): 40 dispositions, implementation evidence, stale assumptions and verification limits.
2. [Business production readiness](BUSINESS_PRODUCTION_READINESS.md): blockers and release acceptance gates.
3. [Local experience plan](LOCAL_EXPERIENCE_PLAN.md): bounded improvements on the existing architecture.
4. [Future scale strategy](design/future_scale_strategy.md): optional investments and objective entry criteria.

CA-01–04 are substantially present: strict local analysis validation, default/custom text framing, non-DEBUG audit snapshots and model strategy/default resolution. The subsequent dev commit also delivers individual-analysis navigation, conservative metric availability and saved original URLs; these are removed from the remaining UX scope. Keep regression coverage and close remaining boundary gaps rather than reimplement them. Repository migration graph head is 0086; this says nothing about deployed DB revision. Historical test counts are not today's green gate.

## Recommended delivery order

| Stage | Packages | Why this order | Exit evidence |
|---|---|---|---|
| A — Rebase the decisions | Recheck fresh dev; agree first business workflow/capability limits; confirm PRD findings still exist. | User is actively changing dev; baseline findings can become stale. | Exact SHA, bounded scope per PR, no duplicate work. |
| B — Close safety/economics blockers | PRD-01 delivery, PRD-02 rights (UX-02 delivered: registry binding, permission gate, run outcomes), PRD-03 spend design; keep publication disabled. | Prevent tenant leakage/global mutations and uncontrolled expense before attracting users. | Cross-tenant/role/registry/concurrency regression tests; approved budget model. |
| C — Make operation repeatable | PRD-04 deploy/restore, PRD-05 truthful outcomes/leases, PRD-06 retention/privacy, PRD-07 AI write boundaries. | A feature-rich bot is not a supported service without recovery and data lifecycle. | Tested production profile, failure/recovery tests, legal/data policy and release packet. |
| D — Reach first trustworthy value | UX-01 onboarding, UX-04 language, read-only UX-05 audit, UX-03 report clarity; PRD-08 visibility. | Reuse current web/agent capabilities; reduce operator-dependent setup. | First-value/recovery journeys, representative latency and usefulness results. |
| E — Managed business pilot | Bounded invited tenants; PRD-09 capability contract and safe-action restrictions; test upgrades and real delivery. | Validate supported workflows, costs and support before self-service. | Signed release gates, restore drill, incident process, pilot feedback and economics. |
| F — Measured optimization/product work | UX-06 spend alerts, UX-07 advice/calibration, UX-08 memory controls, UX-09 filtering; UX-10 export if demanded. | Complete accounting and pilot data make savings/quality decisions meaningful. | Quality/recall versus cost benchmark, customer acceptance, no new hidden side effects. |
| G — Scale/self-service on demand | FUT-01–09 selected by their entry criteria. | Embeddings, billing, tracing and infrastructure are investments, not prerequisites for every deployment. | Separate architecture/product decisions and measured outcomes. |

Some read-only UX work can run alongside safety work if files do not overlap, but the release gates cannot be skipped. No arbitrary calendar/effort promise is made before a fresh scope and workload baseline.

## Current-stage work: one numbered queue

Foundation remains OPEN. The [board's priorities 1–9](design/README.md#prioritized-open-work) are the sole ordered current-work list; [PRD-01–09](BUSINESS_PRODUCTION_READINESS.md#critical-gates-and-work-packages) retain stable acceptance IDs. There are 9 major open readiness areas, not nine remaining PRs or an estimate of all future features. Product expansion phases remain gated.

PR #22 and the bounded general-queue, memory, digest, permissions and privacy follow-ups through #84 are merged; do not restart them or infer release acceptance from those merges. The current first candidate is the normal-job recovery contract/source review. Every-call accounting/reservation design follows the queue contract and requires separate schema/format approval. Ownership comes from the board plus fresh PRs and assigned future lanes, not historical Draft labels.

The ledger retains all original owner/source/patch evidence. No new tests, DB operations, production changes or whole-backlog authorization follow from this planning revision.

## Branch and validation policy

- Use one thematic branch from fresh dev; never push directly to dev or overwrite dirty worktrees.
- Recheck ownership, heads, changed files, dependencies and semantics before publication/integration; preserve intervening owner corrections.
- Run only new fast source/unit checks directly when they need no app bootstrap, DB, network/providers or live state. Batch real-DB/heavy/local checks for the owner; shared test_schema runs stay sequential. Do not repeat finished checks without a concrete changed risk.
- Schema/reset/migration/live/deploy/check changes require separate explicit approval. Full-suite/staging evidence gates a release, not every small package.
- Keep Draft while necessary evidence is pending. Routine bounded Ready/merge uses the standing authorization after fresh compatibility, relevant review and required checks; clarify conflicts, unexpected heads, red checks, unclear ownership/dependencies or elevated risk.

## Preserved constraints and revised assumptions

Keep: deterministic pipeline, PostgreSQL queue, shared-but-isolated workspaces, encrypted secrets, bounded usage, structured outputs, no permanent raw archive by default, human-controlled risky actions.

Revise: “one VPS forever”, “all exports/caches forbidden”, “hosted business always out of scope”, “first four reliability items absent”, “MAX transport means MAX collection”, “all recorded costs constitute a complete daily cap”, and “tier flag means a live feature is ready”. These were either stale observations or product assumptions, not immutable design rules.
