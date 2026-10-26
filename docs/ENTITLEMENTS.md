# Product capability entitlements

P2-T15 foundation, catalog version 1. This is a source contract, not acceptance
or migration/test evidence. Current evidence and resume point live in the
[P2-T15 card](tasks/p2-t15.md) and its PR.

## Scope

A capability is a subsystem-owned, namespaced product right. The collection
subsystem declares `connector.vk.read`, `connector.vk.search` and
`connector.telegram.read`. These coarse product keys deliberately do not mirror
individual VK API methods. Future knowledge, communication, case or document
subsystems declare their own keys and join the registry composition point.
Nothing in the generic decision engine selects a provider or product domain.

`app/services/tenancy/plan_catalog.py` owns versioned grants, beside the existing
resource-limit contract. Catalog v1 grants `*`, without daily quotas, to Starter,
Pro and Business. This is compatibility policy, not the final product packaging.
Only registered keys can match grants; an unknown key is always denied even
under `*`. Patterns support exact keys, `namespace.*` and `*`, not arbitrary glob
expressions. Optional daily quotas use exact capability keys and nonnegative
integer limits. A catalog change must advance its version; existing
`Tenant.PLAN_LIMITS` stays separate and unchanged in this task.

`decide(workspace, required, used_today=..., reserved=..., now=...)` is pure and
returns allowed, required keys, denial reasons, plan and catalog version. It
refuses unknown plans/capabilities, inactive workspaces, expired plans, absent
grants and exhausted quotas. `plan_expires_at` is an aware timestamp; NULL means
no expiry for all existing workspaces. An invalid naive expiry fails closed.
The expiry boundary is inclusive: `now >= plan_expires_at` denies the operation.

## Persistence and execution

Alembic revision 0113 adds nullable `tenants.plan_expires_at` and
`entitlement_usage`. It does not set expiry or change existing plan names.
P2-T16 can populate expiry through a separately verified subscription flow.
Do not start code requiring these columns before the separately approved and
validated migration rollout. Downgrade drops usage evidence; preserve/export
needed audit data before an authorized rollback.

Preview and task admission read rights without consuming them. Admission checks
the effective source snapshot and collection intent in the existing task-run
transaction. Execution rechecks before discovery/search and again before reading
the effective sources, including legacy tasks and queued work. Policy refusal
is a terminal failure, never an automatic retry or a partial successful job.
Existing task/source authorization and personal credential checks still apply.

Usage means **capability admitted for execution**, not successful provider calls,
items fetched or billable LLM tokens. Before external collection, a short
READ COMMITTED transaction takes the same workspace NO KEY UPDATE lock as source
admission, counts UTC-day usage, decides and inserts missing usage rows atomically.
The unique `(tenant_id, job_id, capability)` key makes the two gates and retries
of the same job idempotent. A separate replacement/retry job is a new admission.
A failed provider call does not refund an admission. Previews consume nothing.
A reservation never bypasses changed grants, expiry or workspace activity; it
only prevents recounting the same job against its own daily quota. Locks are
not held across network operations: a revocation does not cancel an already
in-flight request. It is observed at the next execution gate.

Each row retains workspace, capability, job, task, authenticated manual initiator
(or NULL for scheduled work), plan, catalog version and UTC admission time.
Job/task/actor IDs are historical scalar identities, not cascading FKs, so job
retention and task/user deletion cannot silently remove them. Workspace deletion
still removes tenant-owned data. The log stores no provider payloads or secrets.
No independent usage pruning policy is introduced here.

## Inspection

- `GET /api/v1/tasks/entitlements`: current scoped catalog and capability decisions;
  requires existing task-view permission and authenticated workspace membership.
- `POST /api/v1/tasks/plan`: normalized plan plus `entitlements` decision/reasons.
- `python -m cli.main task entitlements --tenant <slug-or-id>`: operator inspection.
- `python -m cli.main task plan '<json>' --tenant <slug-or-id>`: live read-only
  entitlement preview. Without `--tenant` normalization remains offline and
  explicitly says rights are unchecked (`allowed: null`, `workspace_required`).
  Output wraps the canonical `task_plan` and the separate `entitlements` result.
- Chat `task_plan` and `search_plan` include the same scoped decision. A proposal
  is not authorization or evidence of execution and never reserves quota.
- Existing workspace settings comparison and Tenant admin display static policy,
  catalog version and expiry. Static grants are not a live quota decision.

These rights never grant provider API scopes, account access, workspace roles,
LLM payer consent, balances or spend allowances. Existing resource ceilings and
`MAX_WORKSPACE_MEMBERS` continue to apply independently. No checkout/payment,
product packaging, new screen or non-social integration is implemented.

## Deferred validation

The owner explicitly postponed tests and migration application. Required follow-up
covers pure decisions/wildcards/expiry edges, cross-tenant and actor attribution,
all admission routes, queued downgrade/expiry, source and search derivation,
concurrent quota races/idempotency, durable log retention, structured denials,
API/CLI/chat/admin/settings rendering, Alembic upgrade/downgrade and cleanup on
an exclusively owned synthetic PostgreSQL schema. No application/DB/concurrency
PASS, READY, independent audit, production readiness or merge is implied.
