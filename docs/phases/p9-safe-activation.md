# P9 — Safe activation and platform access

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p9) · [full historical notes](../audit/phase-p9-history.md)

**Goal:** External writes activate only through a capability-specific contract.

**Plan — active task cards:**

- [P9-T01](../tasks/p9-t01.md) — one capability-specific activation contract
- [P9-T08](../audit/p9-t08.md) — approval-bound action ledger (BotAction → outbound action); accepted 2026-10-27, integrated in PR #246 (merge `4e74318c`) with migrations 0113–0116

P9-T08 shipped the sealed-approval ledger (actor-bound approvals with
expiry, atomic transitions, durable claim/outcome, read-only admin). The
capability-neutral extension still designs with P9-T01 before enabling any
new external write; `action_send` remains preview-only.

**Accepted history:**

- [P9-T02](../audit/phase-p9-history.md#p9-t02) — platform-owner role `SUPERUSER` with a guarded assignment · 2026-10-25 · `e3943f0`
- [P9-T03](../audit/phase-p9-history.md#p9-t03) — owner-level gates built on the new role · 2026-10-25
- [P9-T04](../audit/phase-p9-history.md#p9-t04) — read-only table registry for the platform owner · 2026-10-25
- [P9-T05](../audit/phase-p9-history.md#p9-t05) — owner access documentation and regression guard · 2026-10-25
- [P9-T06](../audit/phase-p9-history.md#p9-t06) — consistent owner protection across role editing and account management · 2026-10-25 · `e3943f0`

- [P9-T07](../audit/p9-t07.md) — actor-scoped admin catalogs and selected-workspace authorization · 2026-10-27 · `1c760960dfe871693d54ca5ac4e4459140e345f3`

**Phase status:** Active phase; close only after every plan item is accepted.
