# P1 — Truthful queue execution

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md) · [full historical notes](../audit/phase-p1-history.md)

**Goal:** Execution does not start after claim loss, duplicate unresolved effects, or misstate outcomes.

**Plan — active task cards:**

None; accepted scope is summarized below.

**Accepted history:**

- [P1-T01](../audit/phase-p1-history.md#p1-t01) — prove source admission on PostgreSQL across concurrent connections, rollback and ASGI workspace scope · `dab3b5c`
- [P1-T02](../audit/phase-p1-history.md#p1-t02) — invalidate queued task occurrences on definition changes, pause or deletion · 2026-10-22 · `c9ccf9d`
- [P1-T03](../audit/phase-p1-history.md#p1-t03) — distinguish a live queued Job from the task's historical queued receipt

**Phase status:** Closed; accepted on 2026-10-25 at `f55aa2a2c7418e29a642324bf8753bbc53880ad3`.
