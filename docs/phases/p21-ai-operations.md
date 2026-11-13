# P21 — AI model operations and dispatch safety

[Product direction](../product/PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p21)

**Goal:** one managed catalog plus personal/BYOK catalogs, safe dispatch and
attributable usage. This is a plan, not an authorization: no code, deployment,
live provider, payment or public-migration approval is implied. The accepted
planning package is [P21-T01](../audit/p21/p21-t01.md); ROADMAP owns the current
status and priority of the tasks below.

## Owner decisions

The owner decision of 2026-11-13 (OpenCode `ses_e2fa3690affea2bSCgS5dlSVmg`) sets
one user tariff across owned workspaces, configurable module grants, token limits
(including explicit unlimited) and an owned-workspace count limit.
[P2-T17 increment A](../audit/p2/p2-t17.md) supplies the accepted dormant
[holder/policy interface](../platform/SERVICE_PLANS.md). Remaining
period/counting/BYOK-applicability/overage choices stay in the
[P2 activation gates](p2-usage-spend.md#remaining-allowance-activation-gates).

Invited members use the host owner's configured models and functionality subject
to member/object permissions, never their own tariff as an override. Any
workspace role is denied host tariff/catalog/provider/key/model/default
management. BYOK delegation needs explicit owner consent; no secret is exposed
to a member. Accepted personal payer bindings survive until an explicit approved
migration. Corporate billing P2-T08 remains deferred.

## Task plan

Task IDs follow prerequisite order. P0 security surveys come first; the P1
tariff/storage/runtime foundations follow. Independent P0 surveys may run
concurrently. P2-T17 can proceed alongside them, but its holder/policy
deliverable must exist before the dependent consumers.

| Order / task | Prerequisite deliverable | Required handoff |
| --- | --- | --- |
| 1. [P21-T10](../audit/p21/p21-t10.md) Prompt/tool safety — DONE 2026-11-14; accepted `07b30944` | None | Accepted source-backed exposure matrix and fail-closed synthetic reproductions; remediation stays separately scoped. |
| 2. [P21-T11](../audit/p21/p21-t11.md) Credentials — DONE 2026-11-14; accepted `7e11f15f` | None | Monotonic custody/version fence, DNS-pinned egress and synthetic rotation regression evidence; Fernet/D8 unchanged. |
| External: [P2-T17](../audit/p2/p2-t17.md) User tariff | Accepted dormant increment A; [live interface](../platform/SERVICE_PLANS.md) | Holder binding, policy/version/expiry and token definitions; counting/period activation choices remain gated in P2, checkout excluded. |
| 3. [P21-T12](../tasks/p21-t12.md) Dispatch contract | T10 findings, T11 custody, P2-T17 holder interface | Typed contexts and dormant adapter seam with explicit extension interfaces. |
| 4. [P21-T13](../tasks/p21-t13.md) Catalog/grants | T12 contract and P2-T17 tariff storage | Backfilled catalog/grant constraints and owner-only management; dispatch stays off. |
| 5. [P21-T14](../tasks/p21-t14.md) Shared rate control | T12 seam, T13 provider/catalog identity | Stable quota pools, leases, cancellation policy and admission lock order. |
| 6. [P21-T15](../tasks/p21-t15.md) Atomic admission | T14 handoff, P2-T17 allowance policy | Token/USD reservation UOW, immutable evidence and settlement-state contract. |
| 7. [P21-T16](../tasks/p21-t16.md) Settlement/reconciliation | T15 admission storage and contracts | Idempotent accounting, crash recovery and integrated mocked phase check. |

Storage/contract acceptance never depends on later production activation. T12
tests later services through explicit fakes; T13 does not activate managed
routing; T14 proves rate acquisition without requiring a completed T15 ledger.
T15 defines and delivers the state contract T16 consumes. No circular dependency
is hidden behind a requirement to turn on HTTP before its safety foundations
exist.

T12 publishes the normative dispatch contract — resolution order, funding
classes, route matrix, redacted error taxonomy and the extension interfaces for
grants, rate acquisition, atomic admission and settlement:
[LLM_DISPATCH_CONTRACT](../platform/LLM_DISPATCH_CONTRACT.md). T13–T16 extend
that contract by reference; they do not restate it.

## Data shape

| Relation | Rationale |
| --- | --- |
| New `llm_catalogs` | Explicit platform/personal custody within the existing provider/model schema. |
| New `plan_model_grants` | Reusable plan/capability/model availability, default and ordering. |
| New `llm_rate_buckets` | Shared upstream quota state across tenants; social connection buckets have the wrong ownership/FK. |
| Optional `llm_model_health` | Only if measurements justify separating hot health writes from static catalog rows. |
| Existing `llm_*`, scenarios, tenant policy, billing and secret stores | Reuse, preserve accepted evidence and migrate consumers before removing legacy fields. |

Expected baseline: three additional relations and removal of a legacy selection
column only after inventory, mapping and approval. A fourth operational relation
needs measurement. Scope/reference/capability columns may increase; accidental
duplicate configuration should decrease. No existing table is scheduled for
deletion. Every schema change needs named consumers, constraints, migration and
rollback.

## Storage ownership

| Responsibility | Owner |
| --- | --- |
| User plan, holder, module grants and allowance definition | P2-T17; no workspace copies of a subscription. |
| Catalog/provider/model custody, FK migration, capabilities/prices/defaults | T13, consuming T11 secret-source/version contract. |
| Stable upstream pool, fairness and recoverable concurrency leases | T14; token quotas are not provider TPM. |
| Immutable dispatch snapshot and token/USD reservation | T15; reuse personal USD ledger, name missing tenant settlement state. |
| Mutable settlement/reconciliation state and outcome evidence | T16; immutable receipts cannot contain mutable terminal/lease fields. |
| Optional hot model health state | T14 only after measured contention; no fixed table-count target. |

## Verification and activation gate

T16 records the integrated mocked route matrix for chat, analysis, digest, tools,
learning, prompt suggestions and probes: fresh membership/holder/module/grant/
consent/key-version checks, no-HTTP denials, same-class/payer fallback, cross-space
owner token races, rate contention and crash/replay/rollover reconciliation.
Each executor records actual source/documents read, named consumers, focused
commands, tested SHA, isolated-schema migration/retained-data/cleanup evidence
and rollback in its own card/PR. No surrogate coordination task is required.
Owner activation decisions and P2-T09 live verification remain separate; an
integrated mocked pass is not production or capacity certification.

## Numbering

The accepted [P21-T01 audit](../audit/p21/p21-t01.md) keeps its original
numbering; implementation cards were reordered with new unused IDs on owner
request: T02→T12, T03→T15, T04→T16, T05→T14, T06→T11, T07→T10, T08→T13. The
P21-T09 documentation claim was published in `fc35c553`; its temporary card and
row were removed instead of being marked accepted, and its decisions now live in
this phase and in P2-T17. No accepted T01/P2 result is rewritten.
