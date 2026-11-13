# P2 — Complete usage and spend

[Product direction](../product/PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p2) · [full historical notes](../audit/history/phase-p2-history.md)

**Goal:** Paid work is admitted within attributable caps; unknown cost never becomes free.

**Plan — active task cards:**

1. [P2-T15](../audit/p2/p2-t15.md) — **DONE 2026-11-12** · accepted head
   `c4f3f566`, merge `737345d5`; capability decisions, execution gates,
   workspace isolation, quota concurrency/idempotency, durable usage and
   inspection surfaces validated. Existing tenant tier quotas and personal LLM
   payer consent remain separate contracts.
2. [P2-T16](../audit/p2/p2-t16.md) — YooKassa subscription-payment configuration
   foundation; accepted 2026-10-27 (PR #254 head `5de6ff36`, merged
   `e21e373d`). The broader checkout/webhook/activation scope was closed with
   that owner decision and is not tracked. Live purchase activation still needs
   a separately authorized rollout gate.
3. [P2-T09](../tasks/p2-t09.md) — verify real LLM/provider usage admission and
   settlement after its production activation/cutover prerequisites. This lane
   is independent of subscription checkout in T16.
4. [P2-T17](../audit/p2/p2-t17.md) — **DONE 2026-11-14, increment A** · accepted
   `b76a35e6`, verified merge `fd030ad5` ([PR #485](https://github.com/Starck43/social-media-ai/pull/485)).
   Dormant user tariff/sponsorship, module policy and atomic ownership limits;
   [live contract](../platform/SERVICE_PLANS.md). Cutover remains OFF, no AI-path
   increment B, live token pool, checkout or public apply; P21 owns model grants
   and token admission/settlement. Deferred T08 and phase closure remain unchanged.

## Remaining allowance activation gates

Increment A acceptance does not answer or authorize these owner choices:

- Token unit/counting: input/output/cached/reasoning/tool/multimodal usage, including
  failed or partially charged attempts; no conversion from USD/capability counts.
- Period boundary/reset/rollover and expiry/proration: UTC calendar vs paid rolling
  period; plan change must not silently reset an active shared pool.
- Finite package sizes, zero/unlimited packaging and overage: fail closed until
  explicitly configured; no guessed commercial quantities or paid-on-top behavior.
- BYOK applicability and host-sponsored invitee consent: token allowance, USD payer
  consent and credential custody remain distinct.

P21-T13/T15/T16 consume the accepted holder/definition interface, but activation
must resolve these gates and complete separately reviewed dispatch/ledger controls.
AI-path consumer increment B, rollout/signup provisioning and public apply need
their own scoped assignment/authorization; they are not a reopened P2-T17 card.

**Deferred:** [P2-T08](../tasks/p2-t08.md) — corporate/shared billing accounts;
keep the owner's prior deferral. Service-plan entitlements and checkout in T15/T16
do not implement shared/corporate payer accounts.

**Accepted history:**

- [P2-T11](../audit/history/phase-p2-history.md#p2-t11) — align chat, digest and analyzer cost projections with resolved structured provider USD totals, including explicit zero and… · `adf1d6f`
- [P2-T10](../audit/history/phase-p2-history.md#p2-t10) — audit cache-aware token pricing, reported costs, daily caps and payer/workspace attribution
- [P2-T12](../audit/history/phase-p2-history.md#p2-t12) — preserve provider-specific cache usage and version complete dispatch-time cache tariffs
- [P2-T13](../audit/history/phase-p2-history.md#p2-t13) — bind journal tariffs to exact provider/model identity
- [P2-T14](../audit/history/phase-p2-history.md#p2-t14) — use complete spend evidence in caps and payer/workspace reports
- [P2-T01](../audit/history/phase-p2-history.md#p2-t01) — model personal billing account identity and fixed task payer binding; changing payer requires new payer consent · 2026-10-17 · `c18ee2f`
- [P2-T07](../audit/history/phase-p2-history.md#p2-t07) — focused PG/ASGI acceptance and cutover
- [P2-T02](../audit/history/phase-p2-history.md#p2-t02) — admin connection-test payer: paid probes charged to the launching user's personal account · 2026-10-17 · `c18ee2f`
- [P2-T03](../audit/history/phase-p2-history.md#p2-t03) — explicit compatible transition and mapping for legacy finance records and tasks · 2026-10-17 · `c18ee2f`
- [P2-T04](../audit/history/phase-p2-history.md#p2-t04) — atomic idempotent settlement/reconciliation · 2026-10-17 · `689c6fb`
- [P2-T05](../audit/history/phase-p2-history.md#p2-t05) — provision UTC daily baselines · 2026-10-17 · `c18ee2f`
- [P2-T06](../audit/history/phase-p2-history.md#p2-t06) — wire admission through all provider clients and callers · 2026-10-17 · `7e5b4f1`

**Phase status:** Active phase; close only after every plan item is accepted.
