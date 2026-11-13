# User service plans and workspace sponsorship

Accepted dormant [P2-T17 increment A](../audit/p2/p2-t17.md). This is the live
holder/policy contract for developers and operators; the archived task is history.
It does not authorize rollout, payment, public migration apply or token dispatch.

## Subject and authority

The user owns one service plan. `Tenant.plan_holder_user_id` identifies the
sponsoring user; an invited actor never replaces that holder with their own plan.
Workspace membership/role controls object access, not host tariff, catalog,
provider, key, model-default or billing management. Platform-curated tariff
assignment requires fresh platform-owner authority. Membership alone does not
authorize charging/decrypting the holder's BYOK: delegated-use consent and custody
are separate P21 contracts.

## Holder and policy interface

Source: `app/services/tenancy/service_plan_policy.py`.

- `resolve_holder_context(session, tenant_id, actor_user_id, now=..., lock_holder=...)`
  is for authenticated actors. The actor must be an active platform user and an
  active workspace member; even an owner/admin role does not bypass this proof.
- `load_workspace_policy(session, tenant_id, now=..., lock_holder=...)` is only
  for trusted internal workspace scopes without an authenticated actor.
- Both require an active workspace, verified sponsorship, a fresh active holder,
  an active unexpired user plan and its validated immutable policy version.
  Missing/conflicting facts raise `ServicePlanPolicyError` with a bounded `reason`;
  there is no fallback to the actor's tariff, a guessed tier or an unlimited limit.
- Locked resolution locks User before Tenant consumers and re-reads plan state.
  Callers preserve the transaction and recheck execution authority; a context is
  not a perpetual authorization token.

`WorkspacePlanContext` carries:

| Field | Contract |
| --- | --- |
| `actor_user_id` | Authenticated actor, or `None` for a trusted internal lookup. |
| `tenant_id` | Verified workspace scope, not a posted billing override. |
| `plan_holder_user_id` | Sponsoring account, independent of the actor. |
| `plan_code`, `plan_policy_version` | Holder plan and immutable policy identity. |
| `plan_expires_at` | Aware expiry; `None` means no expiry, not an absent plan. |
| `policy` | Validated, deeply frozen module/quota/resource/ownership/token definition. |

P21-T12 passes this typed context by reference and keeps actor, holder,
credential owner, consent version, funding class and payer distinct. P21-T13
owns model grants/availability. P21-T15/T16 own token reservation/settlement;
the context itself performs no consumption or billing.

## Policy and mutation boundaries

`ServicePlanPolicy` owns versioned module grants and exact typed limits. The
existing capability registry/entitlement engine remains authoritative; unknown
capabilities and malformed/incomplete policies deny. Capability admission quotas
remain workspace-scoped; recorded holder/policy identities are historical
attribution, not a token ledger. Workspace USD/source limits may remain stricter
independent ceilings.

For owned-workspace and token limits, **unconfigured is not unlimited**:
configuration plus `None` means explicitly unlimited; zero permits no new usage.
In the DTO, `policy.token_policy is None` means no allowance definition, whereas
`TokenAllowancePolicy(token_limit=None)` means explicitly unlimited. Token unit,
period, counting rules and commercial amounts must not be invented by consumers.

Source: `app/services/tenancy/service_plan_management.py` and
`app/models/managers/tenant_manager.py`.

- Insert a new validated policy revision; do not update/delete old policy or
  backfill evidence. Retained history and restrictive references survive offboarding.
- Assign user plans under fresh operator/holder locks. UI plan/revision changes
  preserve current activity and expiry under the lock and update all sponsored
  workspace mirrors together; they do not buy or renew a subscription.
- `create_owned_workspace` locks the active User, validates policy, counts all
  sponsored spaces (not invited memberships), and inserts workspace plus owner
  membership in one caller-owned transaction. Its explicit session is not committed
  by a nested manager call. Enabled mode rejects generic workspace creation and
  direct sponsorship/plan writes; ownership transfer needs a separate lifecycle.
- Legacy backfill is explicit, complete, confirmed maintenance mapping; ambiguous
  owners/plan state need recorded operator decisions. No first/highest/current-actor
  inference is permitted. Finalization requires matching retained backfill evidence.

## Cutover and rollback

`USER_SERVICE_PLANS_ENABLED` defaults to `false` in `app/core/config.py`. The
disabled branch retains legacy policy reads; `tenants.plan`/expiry are compatibility
mirrors once enabled. Dormant storage must be present before code accesses new
columns; do not treat the feature switch as migration authorization.

Before any enablement: validate the actual target schema, explicitly backfill
sponsorship/user plans, define signup provisioning and finish the mapped consumer
and P21 dispatch controls. A new signup with no assigned user plan fails closed
and rolls its user/workspace transaction back; no implicit free policy is seeded.
No `app/services/ai/**` consumer migration, checkout or live token pool is delivered
by increment A. Keep the switch OFF until separately authorized cutover.

Disabling cutover preserves stored sponsorship and history; do not erase them.
An empty storage downgrade is guarded; retained policy/plan/backfill/admission
data or sponsorship refuses downgrade. Follow [database safeguards](../process/COLLABORATION.md#database)
for any separately authorized apply/rollback. Derive revision IDs and live Alembic
head from migration files, not a stored deployment assumption.

## Open allowance activation decisions

Preserved by [P2](../phases/p2-usage-spend.md#remaining-allowance-activation-gates):
token counting/unit; period boundaries/reset/proration; finite package sizes and
overage; BYOK applicability and delegated-use consent. Storage definitions and
holder interfaces are accepted, but these choices remain unanswered and cannot
be treated as an operational allowance policy.
