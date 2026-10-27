# P6 — Lifecycle and privacy

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p6) · [full historical notes](../audit/phase-p6-history.md)

**Goal:** Retention, export, deletion and offboarding obey scoped privacy contracts.

**Plan — active task cards:**

- [P6-T01](../tasks/p6-t01.md) — Deploy-gated retention rollout
- [P6-T02](../audit/p6-t02.md) — VK OAuth refresh-failure log-privacy contract
  (scoped 2026-10-27 from the legacy one-choice contract); accepted 2026-10-27
  at `1f816ce2` (PR #259), integration pending the owner's merge choice.
- [P6-T05](../audit/p6-t05.md) — Administrator deletion and source admission; accepted 2026-10-27 at `5112dbec`, integrated via merge `9367fb73` (PR #257).

**Accepted history:**

- [P6-T03](../audit/phase-p6-history.md#p6-t03) — add a workspace-admin-only action on the source page to delete only that source's raw staged `CollectedItem` rows · 2026-10-23
- [P6-T04](../audit/phase-p6-history.md#p6-t04) — prevent source deletion from widening an explicitly scoped task to all active workspace sources · 2026-10-22 · `bbb16ba`

**Phase status:** Active phase; close only after every plan item is accepted.
