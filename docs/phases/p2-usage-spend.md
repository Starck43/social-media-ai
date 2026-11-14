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

**Resolved 2026-11-14 by the owner.** The four gates below are answered; the
answers are the operational policy for P21-T15/T16, and a consumer that still
treats them as open is stale. Counting/period/package choices were commercial and
security decisions, so they are recorded here rather than inferred from code.

- **Token unit/counting — all provider-reported billable usage.** One unit: prompt
  input including the cache read and cache write buckets as reported, completion
  output, reasoning and media tokens when the provider reports them. A failed or
  partially charged attempt counts the usage the provider reports for it. When no
  usage is reported, the reserved worst-case ceiling is charged and only
  provider-reported usage for that same attempt may correct it — never a silent
  free call and never a conversion from USD or capability counts.
- **Period — anchored rolling 30-day cycles per holder.** The first consumption
  starts the cycle; it ends 30 days after that instant; the next consumption after
  expiry starts the next cycle. No accumulation, no rollover, no proration. A plan
  change applies the new limit to the already consumed total of the active cycle
  and never resets it. A reservation binds the cycle snapshot taken at admission,
  so an attempt admitted in one cycle settles against that cycle.
- **Packages and overage — finite, hard stop.** Starter 1 000 000 tokens, Pro
  10 000 000, Business explicitly unlimited (`token_limit = NULL`; a missing
  policy or row denies). Exhausting the allowance denies new token-governed
  dispatch until the cycle rolls; there is no paid top-up and no automatic
  downgrade to a cheaper model. Paid overage needs checkout and a billing cycle
  that do not exist, so it remains a separate, unregistered commercial decision.
- **BYOK and delegated use — distinct.** Managed (platform-credential) dispatch
  and host-sponsored invitee dispatch consume the holder's pool. Personal BYOK
  dispatch is recorded as attribution and does not consume it, because it meters
  the holder's own provider cost. Token allowance, USD payer consent and
  credential custody stay separate facts; consenting to a payer is not consenting
  to a credential.

Activation still requires the separately reviewed dispatch/ledger controls of
P21-T15/T16 and an explicit cutover choice. AI-path consumer increment B,
rollout/signup provisioning and public apply keep their own scoped
assignment/authorization; they are not a reopened P2-T17 card.

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
