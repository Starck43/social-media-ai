# P9 — Safe activation and platform access

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p9) · [full historical notes](../audit/phase-p9-history.md)

**Goal:** External writes activate only through a capability-specific contract.

**Plan — active task cards:**

- [P9-T01](../tasks/p9-t01.md) — one capability-specific activation contract
- [P9-T07](../tasks/p9-t07.md) — actor-scoped admin console catalogs
- [P9-T08](../tasks/p9-t08.md) — approval-bound action ledger (BotAction → outbound action)

**Accepted history:**

- [P9-T02](../audit/phase-p9-history.md#p9-t02) — platform-owner role `SUPERUSER` with a guarded assignment · 2026-10-25 · `e3943f0`
- [P9-T03](../audit/phase-p9-history.md#p9-t03) — owner-level gates built on the new role · 2026-10-25
- [P9-T04](../audit/phase-p9-history.md#p9-t04) — read-only table registry for the platform owner · 2026-10-25
- [P9-T05](../audit/phase-p9-history.md#p9-t05) — owner access documentation and regression guard · 2026-10-25
- [P9-T06](../audit/phase-p9-history.md#p9-t06) — consistent owner protection across role editing and account management · 2026-10-25 · `e3943f0`

**Phase status:** Active phase; close only after every plan item is accepted.
