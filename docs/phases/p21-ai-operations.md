# P21 — AI model operations and dispatch safety

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md)

**Status:** planning package; no implementation or activation authorization.

## Goal

Make model selection, provider dispatch, accounting, credentials, limits and
untrusted-input handling explicit without replacing the existing modular-monolith
contracts or creating speculative duplicate tables.

## Planned cards

1. [P21-T01](../tasks/p21-t01.md) — repository review and bounded decomposition.
2. [P21-T02](../tasks/p21-t02.md) — single LLM dispatch seam and resolution contract.
3. [P21-T03](../tasks/p21-t03.md) — atomic admission and quota reservation wiring.
4. [P21-T04](../tasks/p21-t04.md) — settlement, reconciliation and liability closure.
5. [P21-T05](../tasks/p21-t05.md) — provider rate-limit and circuit-breaker wiring.
6. [P21-T06](../tasks/p21-t06.md) — credential boundary and rotation hardening.
7. [P21-T07](../tasks/p21-t07.md) — prompt/tool safety coverage audit.
8. [P21-T08](../tasks/p21-t08.md) — model configuration consolidation and legacy cleanup.

Cards are independently claimable only after their listed predecessors and owner
gates are satisfied. The phase does not authorize renaming `llm_*` tables to the
proposed `ai_*` names, adding a speculative `agents` table, or enabling a new
Redis/runtime owner.
