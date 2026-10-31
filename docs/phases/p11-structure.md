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
7. [P11-T07](../audit/p11-t07.md) — Incremental package-boundary enforcement (R-05) — DONE 2026-10-28 (accepted `f6fbbdaf`, integrated `5eb9c5f6`; PR #268).
8. [P11-T08](../audit/p11-t08.md) — Bounded API lists, safe request correlation and atomic actor/workspace launch budgets — DONE 2026-10-29 (accepted `d1657d1691c4a71570162a1c795d5cafa44a5f49`; PR #278).
9. [P11-T09](../audit/p11-t09.md) — Fail-closed typed agent-tool registration (R-07) — DONE 2026-10-28 (accepted `937a435083e99aa80546510f2cf275fc8101eca9`, integrated `7a89a2b0510ec1588535d80a7adeed0f15ea025f`; PR #273).
10. [P11-T10](../audit/p11-t10.md) — Operator CLI boundaries and machine-output contract — DONE 2026-10-28 (accepted `e1dec81d`, integrated `1506d98d`; PR #267).
11. [P11-T11](../audit/p11-t11.md) — **DONE 2026-10-29**: web/admin decomposition accepted; loader/type-introspection corrections and application/HTTP/DB checks PASS. Accepted `3c793a24c5c9f4c2356663db1dc3f1c4480973fc`; merged `edfd34e321a54147b7d2969418738f0e5f712a8f` without deployment.
12. [P11-T12](../audit/p11-t12.md) — Durable action-audit contract and support correlation (R-10) — DONE 2026-10-29 (contract accepted `b59877ea`, integrated `c8d019ae`, PR #269; persistence accepted and merged `6ab81b14` in `bc41326a`, PR #272 — see [implementation audit](../audit/p11-t12-impl.md); no deployment, retention policy applies from the workspace plan).
13. [P11-T13](../audit/p11-t13.md) — Manager and execution-module decomposition (R-11) — DONE 2026-10-29 (accepted `ecec9ef3`, merged `8b470c08`, PR #276; 76 passed + 10 subtests on real PostgreSQL).
14. [P11-T14](../audit/p11-t14.md) — Canonical model imports and semantic helper reuse (R-12) — DONE 2026-10-28 (accepted `a6568189f890bd225763eb4525b1964893bd50b4`,
    merged `829a8da8aaefe7ccda517cf941260353ee4ed77b`; PR #275).
15. [P11-T15](../audit/p11-t15.md) — Reproducible package and configuration contract (R-13) — DONE 2026-10-27 (accepted `2c4c6e5566a61f6a9b02967db1a152e4fa1140e7`).
16. [P11-T16](../audit/p11-t16.md) — Test and reference navigation without evidence loss (R-14) — DONE 2026-10-27 (accepted `bd6120cfb698f0bc71481660c4356312985068cf`).
17. [P11-T17](../audit/p11-t17.md) — Localizable presentation boundaries (R-15) — DONE 2026-10-29 (accepted `de03fc20`, merged `c20edcab`, PR #282; Russian-only catalog with `ru` fallback and stable message IDs at presenter/LLM error boundaries).
18. [P11-T18](../audit/p11-t18.md) — DONE 2026-10-31: specification accepted; implementation requires a selected capability (accepted `2245dc46508f5c68f03dc3e1354a134ee851a573`, merged `fbcc3e3bd665847ad33a211bff949f4c9e79e4b7`; PR #283).
19. [P11-T19](../audit/p11-t19.md) — DONE 2026-10-31: specification accepted; implementation/measurement gates remain (accepted `c55c07df96fa7c9d98f06377e8a641d4b92102da`, merged `10e74925e1bb552cf66e255d1ef2d95a4d9a8dfd`; PR #284).
20. [P11-T20](../audit/p11-t20.md) — Select and specify bounded enterprise requirements (R-18) — DONE 2026-10-27; owner retained the current identity scope (accepted PR #265, `f4ecdceb`).

21. [P11-T21](../audit/p11-t21.md) — minimal task-reading path and removal of redundant audit navigation — DONE 2026-10-27 (accepted `401ee050`).
22. [P11-T22](../audit/p11-t22.md) — host shared working principles in the project rules — DONE 2026-10-27 (accepted `59027292`).
23. [P11-T23](../audit/p11-t23.md) — Split agent-task documentation by reader — DONE 2026-10-29 (accepted `363a26e1`; owner-waived tests, docs-only).
24. [P11-T24](../audit/p11-t24.md) — Reproduce schema guard objects in the metadata-built test schema — DONE 2026-10-28 (rebuilt-schema billing 46/46, drift test deterministic).
25. [P11-T25](../audit/p11-t25.md) — Realign the model-layout fixtures with the migrated schema — DONE 2026-10-28 (accepted `a6568189f890bd225763eb4525b1964893bd50b4`,
    merged `829a8da8aaefe7ccda517cf941260353ee4ed77b`; PR #275).

26. [P11-T26](../audit/p11-t26.md) — DONE 2026-10-31: canonical model paths; obsolete shims removed; import/layout, fixture and offline checks passed (accepted `f955448f8387aca45a4475efeb2d2cd44ad18928`, merged `b8f372f0de16a0f512a0b6802367573737f78329`; PR #285).
27. [P11-T27](../audit/p11-t27.md) — DONE 2026-10-31: admin console hardening and live integration verification accepted at `1d740aa3`; merged by PR #286 at `d66e9f8c8ff1a313f5350b674c50f1ef651e1c29`.
28. [P11-T28](../audit/p11-t28.md) — DONE 2026-10-31: workspace deletion reserved to superuser; retained audit protected; merged with P11-T27 by PR #286.
29. [P11-T29](../tasks/p11-t29.md) — Durable workspace offboarding audit and explicit retention contract.

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
