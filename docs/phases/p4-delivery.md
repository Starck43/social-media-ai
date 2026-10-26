# P4 — Tenant-safe delivery

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p4) · [full historical notes](../audit/phase-p4-history.md)

**Goal:** Delivery is tenant-safe, retry-safe and truthful about uncertain outcomes.

**Plan — active task cards:**

- [P4-T01](../tasks/p4-t01.md) — staging operator-recovery evidence
- [P4-T02](../audit/p4-t02.md) — notification recipients, per-user read state and lifecycle; accepted 2026-10-27, integrated in PR #246 (merge `4e74318c`)

P4-T02 shipped the recipient-specific inbox semantics decided in the PR #246
coordination: `notification_receipts` per user, `severity`, addressed
delivery and per-recipient read state; retention purging stays a P6-T02
contract item.

**Accepted history:**

- No recorded task history.

**Phase status:** Active phase; close only after every plan item is accepted.
