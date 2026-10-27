# P11 — Structure, interfaces and scale readiness

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p11)

**Goal:** A modular monolith with shared application use-cases behind web/API/CLI/
agent tools, explicit transactions, compatible contracts and measured growth.
No task in this phase authorizes runtime topology changes or a broad rewrite.

## Numbered plan and active cards

1. [P11-T01](../audit/p11-t01.md) — structural audit and reconciliation — DONE 2026-10-27 (accepted `58f6ddfb`).
2. [P11-T02](../audit/p11-t02.md) — register follow-ups, queue priority and complexity — DONE 2026-10-27 (accepted `08fa36d4`).
3. [P11-T03](../audit/p11-t03.md) — Offline CI safety net and supported baseline (R-01) — DONE 2026-10-27 (accepted `0d154532`).
4. [P11-T04](../audit/p11-t04.md) — Shared canonical task-plan CREATE preparation across five adapters — DONE 2026-10-27 (accepted `ef2f7030d9f5f8e6b8e3fe0e53c5734dbaf10cfe`, integrated `8fc84585a9f188b48799e02984fd12f4f2ac73da`; PR #263).
5. [P11-T05](../audit/p11-t05.md) — Explicit CREATE transaction ownership and recoverable post-commit steps — DONE 2026-10-27 (accepted `393b73bb15cddf57e23b0b8a15514aea8e1286e5`, integrated `bb8d4385cee477fab39d52ad73a68936400f0ab6`; PR #264).
6. [P11-T06](../audit/p11-t06.md) — Typed launch contracts, safe errors and allowlisted receipts — DONE 2026-10-27 (accepted `d2c5c91936766715c71dbdebb559aaa814dae346`, integrated `1ce7dcb8d53faaa1b6b6a3bb7e4e086d60c8bfba`; PR #266).
7. [P11-T07](../tasks/p11-t07.md) — Incremental package-boundary enforcement (R-05).
8. [P11-T08](../tasks/p11-t08.md) — Public API compatibility and bounded request policy (R-06).
9. [P11-T09](../tasks/p11-t09.md) — Fail-closed typed agent-tool registration (R-07).
10. [P11-T10](../audit/p11-t10.md) — Operator CLI boundaries and machine-output contract — DONE 2026-10-28 (accepted `e1dec81d`, integrated `1506d98d`; PR #267).
11. [P11-T11](../tasks/p11-t11.md) — Web and admin use-case decomposition (R-09).
12. [P11-T12](../tasks/p11-t12.md) — Durable action-audit contract and support correlation (R-10).
13. [P11-T13](../tasks/p11-t13.md) — Manager and execution-module decomposition (R-11).
14. [P11-T14](../tasks/p11-t14.md) — Canonical model imports and semantic helper reuse (R-12).
15. [P11-T15](../audit/p11-t15.md) — Reproducible package and configuration contract (R-13) — DONE 2026-10-27 (accepted `2c4c6e5566a61f6a9b02967db1a152e4fa1140e7`).
16. [P11-T16](../audit/p11-t16.md) — Test and reference navigation without evidence loss (R-14) — DONE 2026-10-27 (accepted `bd6120cfb698f0bc71481660c4356312985068cf`).
17. [P11-T17](../tasks/p11-t17.md) — Localizable presentation boundaries (R-15).
18. [P11-T18](../tasks/p11-t18.md) — Typed connector and job extension seams (R-16).
19. [P11-T19](../tasks/p11-t19.md) — Capacity telemetry and reproducible scale evidence (R-17).
20. [P11-T20](../audit/p11-t20.md) — Select and specify bounded enterprise requirements (R-18) — DONE 2026-10-27; owner retained the current identity scope (accepted PR #265, `f4ecdceb`).

21. [P11-T21](../audit/p11-t21.md) — minimal task-reading path and removal of redundant audit navigation — DONE 2026-10-27 (accepted `401ee050`).
22. [P11-T22](../audit/p11-t22.md) — host shared working principles in the project rules — DONE 2026-10-27 (accepted `59027292`).
23. [P11-T23](../tasks/p11-t23.md) — Split agent-task documentation by reader — TODO (registered 2026-10-27; waits for P11-T16, P8-T40 and P8-T41).
24. [P11-T24](../tasks/p11-t24.md) — Reproduce schema guard objects in the metadata-built test schema — IN_PROGRESS (registered 2026-10-28; the live `test_schema` repair becomes reproducible).

## Sequencing and continuity

This parent plan is optional navigation, not a prerequisite read for each card.
Task findings/acceptance and exact contract pointers are task-local; the root
audit overview is retired. Snapshot statistics and full reconciliation stay in
pinned Git history linked from [P11-T01's audit](../audit/p11-t01.md), never in the execution read path.

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
