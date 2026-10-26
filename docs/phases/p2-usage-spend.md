# P2 — Complete usage and spend

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p2) · [full historical notes](../audit/phase-p2-history.md)

**Goal:** Paid work is admitted within attributable caps; unknown cost never becomes free.

**Plan — active task cards:**

1. [P2-T15](../tasks/p2-t15.md) — define and enforce plan entitlements for
   connector operations. Existing tenant tier quotas and personal LLM payer
   consent remain separate contracts.
2. [P2-T16](../tasks/p2-t16.md) — add sandbox-tested, self-service subscription
   checkout and lifecycle on top of the T15 entitlement contract. Live purchase
   activation needs a separately authorized rollout gate.
3. [P2-T09](../tasks/p2-t09.md) — verify real LLM/provider usage admission and
   settlement after its production activation/cutover prerequisites. This lane
   is independent of subscription checkout in T16.

**Deferred:** [P2-T08](../tasks/p2-t08.md) — corporate/shared billing accounts;
keep the owner's prior deferral. Service-plan entitlements and checkout in T15/T16
do not implement shared/corporate payer accounts.

**Accepted history:**

- [P2-T11](../audit/phase-p2-history.md#p2-t11) — align chat, digest and analyzer cost projections with resolved structured provider USD totals, including explicit zero and… · `adf1d6f`
- [P2-T10](../audit/phase-p2-history.md#p2-t10) — audit cache-aware token pricing, reported costs, daily caps and payer/workspace attribution
- [P2-T12](../audit/phase-p2-history.md#p2-t12) — preserve provider-specific cache usage and version complete dispatch-time cache tariffs
- [P2-T13](../audit/phase-p2-history.md#p2-t13) — bind journal tariffs to exact provider/model identity
- [P2-T14](../audit/phase-p2-history.md#p2-t14) — use complete spend evidence in caps and payer/workspace reports
- [P2-T01](../audit/phase-p2-history.md#p2-t01) — model personal billing account identity and fixed task payer binding; changing payer requires new payer consent · 2026-10-17 · `c18ee2f`
- [P2-T07](../audit/phase-p2-history.md#p2-t07) — focused PG/ASGI acceptance and cutover
- [P2-T02](../audit/phase-p2-history.md#p2-t02) — admin connection-test payer: paid probes charged to the launching user's personal account · 2026-10-17 · `c18ee2f`
- [P2-T03](../audit/phase-p2-history.md#p2-t03) — explicit compatible transition and mapping for legacy finance records and tasks · 2026-10-17 · `c18ee2f`
- [P2-T04](../audit/phase-p2-history.md#p2-t04) — atomic idempotent settlement/reconciliation · 2026-10-17 · `689c6fb`
- [P2-T05](../audit/phase-p2-history.md#p2-t05) — provision UTC daily baselines · 2026-10-17 · `c18ee2f`
- [P2-T06](../audit/phase-p2-history.md#p2-t06) — wire admission through all provider clients and callers · 2026-10-17 · `7e5b4f1`

**Phase status:** Active phase; close only after every plan item is accepted.
