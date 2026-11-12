# P21 — AI model operations and dispatch safety

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p21)

## Goal and limits

Review and improve the existing modular-monolith foundations for thousands of
users and hundreds of concurrent requests. Support superuser-curated plan models
alongside personal/BYOK models, without per-user copies of managed configuration.
This is a planning package, not shipped behavior, proof of capacity/security,
subscriber-billing approval or permission to apply public migrations.

## Data-shape recommendation

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
duplicate configuration should decrease. No existing table is scheduled for deletion.
Every schema change needs named consumers, constraints, migration and rollback.

## Numbered plan

1. [P21-T01](../tasks/p21-t01.md) — review and register the bounded package.
2. [P21-T02](../tasks/p21-t02.md) — one dispatch, access, payer and fallback contract.
3. [P21-T03](../tasks/p21-t03.md) — atomic admission and allowance reservations.
4. [P21-T04](../tasks/p21-t04.md) — settlement, orphan detection and reconciliation.
5. [P21-T05](../tasks/p21-t05.md) — shared-key rate limits, concurrency and backpressure.
6. [P21-T06](../tasks/p21-t06.md) — credential custody, egress and rotation safeguards.
7. [P21-T07](../tasks/p21-t07.md) — untrusted-input/tool safety coverage audit.
8. [P21-T08](../tasks/p21-t08.md) — shared catalog/grants and validated generation policy.

T02 is normative. T03/T04 and T05 integrate its boundaries; T06/T07 can audit
independently. T08 migrates catalog structure without dispatch activation, then
enables managed routing only after its runtime/credential predecessors pass.
Coordinate overlapping files through task branches; independent cards are not
authorization for conflicting writers. Existing P2 acceptance and production-only
P2-T09 remain intact; P2-T08 shared corporate billing stays owner-deferred.

## Decisions before activation

The subscription subject, included allowance units/period, overage rules and any
personal-key workspace sharing need explicit owner choices. Until then preserve
accepted payer consent and no-new-charging behavior. Managed credentials remain
deployment secrets; BYOK remains encrypted with owner authorization. Fallback
never silently changes funding class/payer. Reuse the existing runtime and
notification/logging seams; new Redis infrastructure or webhook routes are not
implied by this plan.
