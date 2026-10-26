# P4 — Tenant-safe delivery

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p4) · [full historical notes](../audit/phase-p4-history.md)

**Goal:** Delivery is tenant-safe, retry-safe and truthful about uncertain outcomes.

**Plan — active task cards:**

- [P4-T01](../tasks/p4-t01.md) — staging operator-recovery evidence
- [P4-T02](../tasks/p4-t02.md) — notification recipients, per-user read state and lifecycle

P4-T02 extends delivery with recipient-specific inbox semantics for the business
assistant. First decide the receipt/lifecycle contract; only then change schema,
bell/admin behavior and migration. P10-T26 manager cleanup does not implement it.

**Accepted history:**

- No recorded task history.

**Phase status:** Active phase; close only after every plan item is accepted.
