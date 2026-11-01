# Self-service subscriptions — P2-T16 configuration contract

Configuration foundation only. YooKassa is selected; checkout, provider requests,
payment persistence and subscription activation were not included in the accepted
increment. The owner closed P2-T16 at this scope on 2026-10-27; broader lifecycle
work is not an active task and must be registered before implementation. See the
[P2-T16 acceptance record](audit/p2-t16.md).

## Source snapshot and dependency

Planning snapshot inspected before P2-T16 integration: dev
`6ff77b4447e11c92ed2d49f7ade8b76c3c37b852`; included claim base
`5c3c6f65f15ad7a33525253ea153de68f68932e2`. That snapshot predates the accepted
provider-configuration increment and showed operator-managed tenant tiers but no
selected subscription provider or verified checkout/webhook implementation. On
the integrated source, the selected provider and safe defaults are recorded in
the [P2-T16 acceptance](audit/p2-t16.md); checkout/webhook implementation is not
claimed here. Use existing registration, workspace-membership and plan-comparison
surfaces if a separately registered implementation is approved.

P2-T15's entitlement/expiry contract was in deferred PR #253
(`b60998200d5216e6032a58b38a19030324bda699` at planning time). As of the
2026-10-27 refresh, the owner-approved sequence #246 → #252 → #253 was integrated
into dev (merges `4e74318c`, `83c1f79a`, `67acbd53`) and migration 0117 was the
integrated head; those fields/services are consumed, not copied from a branch.
P2-T16 was accepted separately as a configuration-only increment.

## Remaining lifecycle decisions before checkout implementation

1. Confirm whether the first checkout is one-off period purchase, automatic
   renewal or both; define cancellation and downgrade effective dates explicitly.
   Do not activate recurring payments without an explicit consent contract.
2. Define configurable offers (plan, period, currency, price, version and payment
   environment). Do not finalize capability packaging or real prices in code;
   test-only synthetic offers must never become live purchasable offers.
3. If a separately registered implementation is approved, recheck the integrated
   migration graph and reserve a distinct revision on its actual head. This plan
   does not reserve a number or create a dependency on absent revisions.

## Selected provider and official contract

Owner delegated the choice to any provider operating in Russia. Selected:
**YooKassa API v3**, initial boundary contract `yookassa-v3-test-v1`. Provider
selection is now resolved; references to a missing selection in the original
planning snapshot are historical. The new settings and `.env.example` block
select it by default, keep checkout disabled and accept test mode only. No final
prices, production payment rollout or automatic debit consent are inferred.

Read the official [quick start](https://yookassa.ru/developers/payment-acceptance/getting-started/quick-start),
[interaction format](https://yookassa.ru/developers/using-api/interaction-format),
[notifications](https://yookassa.ru/developers/using-api/webhooks) and
[test-store contract](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing).

- Fixed API host `https://api.yookassa.ru/v3`. Test-store credentials use the same
  host as live credentials. HTTP Basic Auth uses shop ID and secret. No user or
  webhook-supplied API base URL may receive credentials.
- Payment creation: server-side `POST /payments`, `capture=true`, redirect
  confirmation with an approved configured return URL. Never request stored
  payment methods or recurrent debits without a separately approved consent flow.
- `Idempotence-Key` has at most 64 characters; YooKassa guarantees idempotency for
  only 24 hours. Keep the purchase's key/immutable request and never blindly issue
  another create after that window. Timeout/500 means an uncertain outcome;
  reconcile rather than treat it as failed or retry with a fresh key.
- Supported event contract for the future handler: `type=notification`,
  `payment.succeeded` or `payment.canceled`, and a bounded payment object ID.
  Unknown events never activate anything. HTTP Basic notifications are configured
  in the Merchant Profile; there is no invented shared signing header in this
  contract. A notification is a reconciliation hint, not authority.
- Before a future activation, retrieve `GET /payments/{payment_id}` with the
  server's credentials and verify the response ID, recipient account_id, exact
  expected amount/currency, `test=true`, and stored opaque purchase binding in
  metadata. Only a confirmed `succeeded`/`paid=true` object can be considered.
  The received event's status and browser return URL alone are never sufficient.
- Notification retries: the provider requires HTTP 200 acknowledgement and retries
  other statuses for up to 24 hours. A future handler must not acknowledge an
  activation as committed before its durable transaction succeeds. Public callback
  handling needs bounded bodies, abuse controls and safe error handling; no route
  is exposed in this increment.

## Entitlement migration dependency

P2-T15's revision `0117` introduced nullable `tenants.plan_expires_at` and
`entitlement_usage`; this scope must consume, not recreate or replace, them. That
numbered history was folded into the Q-14 frozen `baseline_0118`; `0119` is the
checked-in follow-up for workspace offboarding. Q-14 verified the local public
and test schemas against the baseline, not every deployment target. Follow
[DEPLOYMENT](DEPLOYMENT.md) for target-state inspection and migration transition.

Any separately approved subscription schema must reserve a distinct revision
from the actual integrated head after rechecking the graph. No migration number
is reserved by this document, and no migration is added here. A missing
schema/prerequisite must fail closed at future activation, not trigger runtime
DDL or silently grant plan access without expiry.

## Required security and lifecycle contract

- Only an authorized actor in the owning workspace may initiate or inspect a
  purchase. Payment identity must not be confused with an LLM payer account.
- Persist an immutable expected offer/amount/currency/environment and opaque
  purchase identity before a provider interaction. Do not trust a client-supplied
  plan, amount, browser return URL, workspace ID or payment success flag.
- Checkout creation and activation need explicit idempotency boundaries and DB
  constraints/atomic transitions, including concurrent duplicate requests.
- The selected provider's verified payment evidence must match the stored
  purchase, merchant/environment, amount and currency before granting anything.
  Failed, pending, unknown or mismatched payment never grants entitlements.
- Webhook authenticity, replay, duplicate/out-of-order events, event retrieval,
  retries and reconciliation follow the selected provider's actual contract.
  Do not assume webhook possession alone proves payment success.
- Define purchase/subscription/receipt states and renewal, cancellation, expiry
  and downgrade behavior. Prevent stale events from reactivating revoked access.
- Update the accepted P2-T15 entitlement/expiry contract atomically with the
  subscription transition. Queue execution must still recheck current rights.
- Reuse existing settings/registration surfaces. Show owner-visible payment and
  subscription receipts without exposing credentials or provider secrets.
- Personal LLM consent, balances/settlement, spend caps, provider API scopes,
  workspace roles and MAX_WORKSPACE_MEMBERS stay independent. P2-T08 shared
  payer accounts and P2-T09 live LLM billing verification are not implemented.

## Validation and execution boundaries

No tests, database access, migration application, runtime startup, external
provider calls, deployment or subagents were performed while drafting this
planning document. The separate P2-T16 configuration checks and acceptance are
recorded in the [P2-T16 audit](audit/p2-t16.md); they do not prove checkout
functionality or readiness. P2-T15's deferred behavioral tests remain unrun; this
document or unrelated green checks do not replace them.

Provider selection and the official test contract are recorded in the PR;
checkout persistence and lifecycle implementation remain incomplete. Before running tests establish their permitted scope, read tests/AGENTS
and obtain any required environment authorization. No real payment, automatic
renewal, public-schema mutation or production activation is permitted here.
Use isolated mocks/synthetic evidence for authorized tests; provider sandbox
calls, credentials and writes require an explicit applicable scope.

Independent acceptance, integration and production rollout are separate gates.
Pin the review SHA before audit; later shared-file/migration changes need a PR
comment and new revision-specific evidence. Preserve the paused P2-T15 worktrees.
