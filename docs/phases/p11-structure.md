# P11 — Structure, interfaces and scale readiness

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p11)

**Goal:** A modular monolith with shared application use-cases behind web/API/CLI/
agent tools, explicit transactions, compatible contracts and measured growth.
No task in this phase authorizes runtime topology changes or a broad rewrite.

## Numbered plan and active cards

1. [P11-T01](../tasks/p11-t01.md) — structural audit and reconciliation ([source plan](../../REFACTORING_PLAN.md)).
2. [P11-T02](../tasks/p11-t02.md) — register follow-ups, queue priority and complexity.
3. [P11-T03](../tasks/p11-t03.md) — Offline CI safety net and supported baseline (R-01).
4. [P11-T04](../tasks/p11-t04.md) — Shared task application orchestration (R-02).
5. [P11-T05](../tasks/p11-t05.md) — Explicit task transaction ownership (R-03).
6. [P11-T06](../tasks/p11-t06.md) — Typed use-case contracts, safe errors and presenters (R-04).
7. [P11-T07](../tasks/p11-t07.md) — Incremental package-boundary enforcement (R-05).
8. [P11-T08](../tasks/p11-t08.md) — Public API compatibility and bounded request policy (R-06).
9. [P11-T09](../tasks/p11-t09.md) — Fail-closed typed agent-tool registration (R-07).
10. [P11-T10](../tasks/p11-t10.md) — Operator CLI boundaries and machine-output contract (R-08).
11. [P11-T11](../tasks/p11-t11.md) — Web and admin use-case decomposition (R-09).
12. [P11-T12](../tasks/p11-t12.md) — Durable action-audit contract and support correlation (R-10).
13. [P11-T13](../tasks/p11-t13.md) — Manager and execution-module decomposition (R-11).
14. [P11-T14](../tasks/p11-t14.md) — Canonical model imports and semantic helper reuse (R-12).
15. [P11-T15](../tasks/p11-t15.md) — Reproducible package and configuration contract (R-13).
16. [P11-T16](../tasks/p11-t16.md) — Test and reference navigation without evidence loss (R-14).
17. [P11-T17](../tasks/p11-t17.md) — Localizable presentation boundaries (R-15).
18. [P11-T18](../tasks/p11-t18.md) — Typed connector and job extension seams (R-16).
19. [P11-T19](../tasks/p11-t19.md) — Capacity telemetry and reproducible scale evidence (R-17).
20. [P11-T20](../tasks/p11-t20.md) — Select and specify bounded enterprise requirements (R-18).

## Sequencing and continuity

The owner requested all 18 follow-ups be registered on 2026-10-27, superseding
one-at-a-time registration in the original audit draft. Execution is still
claimed one dependency-ready task at a time. ROADMAP owns current ordering,
complexity, status and owner; cards own scope/acceptance and predecessor links.

First safety net → shared use-case → explicit transactions → typed/layered
boundaries. Cross-cutting adapter and manager work uses the extracted seams.
P8-T39/T40 retain user-journey/composition work; P8-T33/T34 retain connector
pilots; P2-T08/Q-12 retain their explicit deferrals. Conditional enterprise,
locale and capacity tasks begin with selection/specification, not activation.
Move accepted results to docs/audit using the normal independent-review lifecycle;
registration/integration is not implementation acceptance.

**Phase status:** Active; close only after every selected item is accepted or
explicitly descoped/transferred by the owner with recorded continuity.
