# Self-service subscriptions — P2-T16 contract draft

Planning only. No checkout, payment integration or subscription activation is
implemented or accepted by this document. Track decisions in the linked task
PR; current status/owner live in [ROADMAP](../ROADMAP.md), scope in the
[P2-T16 card](tasks/p2-t16.md).

## Current source and dependency

Inspected dev: `6ff77b4447e11c92ed2d49f7ade8b76c3c37b852`.
Included claim base: `5c3c6f65f15ad7a33525253ea153de68f68932e2`.
Use existing registration, workspace membership and plan-comparison surfaces.
Current source has operator-managed tenant tiers but no selected subscription
payment provider or verified checkout/webhook implementation.

P2-T15 defines the entitlement/expiry contract in deferred PR #253, currently
`b60998200d5216e6032a58b38a19030324bda699`; those fields/services are not in dev.
Do not copy, merge or assume acceptance of that branch here. The existing owner
integration sequence remains #246 → #252 → #253, each after its own acceptance.
P2-T16 has no implicit place or merge authorization in that sequence.

## Decisions required before implementation

1. Owner selects the payment provider. Then read its official sandbox,
   callback-authenticity and event-retrieval documentation and record the exact
   supported event contract. Do not invent signature headers or status mappings.
2. Confirm whether the first checkout is one-off period purchase, automatic
   renewal or both; define cancellation and downgrade effective dates explicitly.
   Do not activate recurring payments without an explicit consent contract.
3. Define configurable offers (plan, period, currency, price, version and payment
   environment). Do not finalize capability packaging or real prices in code;
   test-only synthetic offers must never become live purchasable offers.
4. Establish how accepted P2-T15 is consumed after integration, then reserve a
   free Alembic revision against fresh dev and open PRs. No number is reserved
   by this planning document and no dependency on absent revisions is created.

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

Before implementation, record the owner-approved provider and sandbox contract
in the PR. Before running tests establish their permitted scope, read tests/AGENTS
and obtain any required environment authorization. No real payment, automatic
renewal, public-schema mutation or production activation is permitted here.
Use isolated mocks/synthetic evidence for authorized tests; provider sandbox
calls, credentials and writes require an explicit applicable scope.

Independent acceptance, integration and production rollout are separate gates.
Pin the review SHA before audit; later shared-file/migration changes need a PR
comment and new revision-specific evidence. Preserve the paused P2-T15 worktrees.
