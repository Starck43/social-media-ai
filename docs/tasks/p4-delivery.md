# P4 — Tenant-safe delivery (gate: delivery stays tenant-safe)

Phase scope and accepted-task history. [Active index](../../ROADMAP.md#p4);
detailed cards are siblings in this directory. [Product direction](../PRODUCT_PLAN.md#foundation-directions).
Historical instructions/permissions below are evidence, not current policy.

Goal: successful recipients are not resent; ambiguous sends stop for review;
recipients and credentials belong to the owning workspace.
Delivered: legacy partial-delivery stop, zero-success terminal outcome,
scheduled/forced publisher fences and lease-loss evidence (PRs #106, #112,
#113, #115, #120).

## Closed tasks
