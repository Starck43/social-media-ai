# Self-service subscriptions — P2-T16 contract draft

Configuration foundation only. YooKassa is selected; no checkout, provider
request, payment persistence or subscription activation is implemented or accepted
by this increment. Track decisions in the linked task
PR; current status/owner live in [ROADMAP](../ROADMAP.md), scope in the
[P2-T16 card](tasks/p2-t16.md).

## Current source and dependency

Inspected dev: `6ff77b4447e11c92ed2d49f7ade8b76c3c37b852`.
Included claim base: `5c3c6f65f15ad7a33525253ea153de68f68932e2`.
Use existing registration, workspace membership and plan-comparison surfaces.
Current source has operator-managed tenant tiers but no selected subscription
payment provider or verified checkout/webhook implementation.

P2-T15's entitlement/expiry contract was in deferred PR #253
(`b60998200d5216e6032a58b38a19030324bda699` at planning time). As of this
refresh (2026-10-27) the owner-approved sequence #246 → #252 → #253 is
integrated into dev (merges `4e74318c`, `83c1f79a`, `67acbd53`) and migration
0117 is the current integrated head; those fields/services are now in dev and
are consumed by this task, not copied from any branch.
P2-T16 was never part of that sequence and still has no implicit merge
authorization beyond the owner's per-PR decisions.

## Remaining lifecycle decisions before checkout implementation

1. Confirm whether the first checkout is one-off period purchase, automatic
   renewal or both; define cancellation and downgrade effective dates explicitly.
   Do not activate recurring payments without an explicit consent contract.
2. Define configurable offers (plan, period, currency, price, version and payment
   environment). Do not finalize capability packaging or real prices in code;
   test-only synthetic offers must never become live purchasable offers.
3. Establish how accepted P2-T15 is consumed after integration, then reserve a
   free Alembic revision against fresh dev and open PRs. No number is reserved
   by this planning document and no dependency on absent revisions is created.

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

## Migration 0117 dependency (refreshed 2026-10-27)

P2-T15's `0117` owns nullable `tenants.plan_expires_at` and
`entitlement_usage`; this task must consume, not recreate or replace, them.
After #246 (0113–0116) integrated, 0117 was re-parented onto 0116 and merged
as `67acbd53`; it is the current single alembic head, and it was applied to the
local dev DB per the owner's 2026-10-27 integration instruction.

Refresh performed on integrated dev: 0117 confirmed present in the actual
migration graph. When the subscription purchases/events/receipts schema design
is approved, reserve a distinct free revision on the actual integrated head —
0118 is free at this refresh — and inspect the graph again before reserving.
No 0118 or other number is reserved by this increment, and no migration is
added now. A missing schema/prerequisite must fail closed at future
activation, not trigger runtime DDL or silently grant plan access without
expiry.

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
provider calls, deployment or subagents were performed for this draft. It is
not evidence of checkout functionality or readiness. P2-T15's unrun tests remain
unrun; neither this assignment nor green checks from other work replace them.

Provider selection and the official test contract are recorded in the PR;
checkout persistence and lifecycle implementation remain incomplete. Before running tests establish their permitted scope, read tests/AGENTS
and obtain any required environment authorization. No real payment, automatic
renewal, public-schema mutation or production activation is permitted here.
Use isolated mocks/synthetic evidence for authorized tests; provider sandbox
calls, credentials and writes require an explicit applicable scope.

Independent acceptance, integration and production rollout are separate gates.
Pin the review SHA before audit; later shared-file/migration changes need a PR
comment and new revision-specific evidence. Preserve the paused P2-T15 worktrees.
