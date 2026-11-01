# P12 — Domain workspace and navigation expansion

[Product direction](../PRODUCT_PLAN.md#expansion-sequence) · [ROADMAP](../../ROADMAP.md#p12)

**Goal:** Scale the `/app` web workspace from the initial social-monitoring vertical into a domain-grouped navigation shell and bounded management surfaces for the expansion sequence (`Research/Connectors`, `Knowledge/Documents`, `Communication/Follow-up`, and `Cases/Approvals`), while preserving chat-first execution, `WebPerms` gating, and `ready=False` rollout flags.

**Plan — domain workspace expansion (ordered):**

1. [P12-T01](../tasks/p12-t01.md) — domain-grouped `/app` sidebar navigation (`NavItem.group`) and `ready=False` (`· скоро`) placeholders for upcoming expansion sections.
2. [P12-T02](../tasks/p12-t02.md) — Connectors & Research catalog (`/app/connectors`): capability matrix, redacted diagnostic health, incremental sync watermarks, and paired local gateway controls.
3. [P12-T03](../tasks/p12-t03.md) — Knowledge & Documents workspace (`/app/knowledge`, `/app/documents`): ACL-filtered retrieval, page/cell/message provenance citations, OCR/freshness boundaries, and versioned document state.
4. [P12-T04](../tasks/p12-t04.md) — Communication & Follow-up workspace (`/app/communications`, `/app/followups`): verified contacts/threads, actor-bound scheduled outbox with TTL and kill switch, and quiet-hours follow-up reminders.
5. [P12-T05](../tasks/p12-t05.md) — Cases, supplier comparison & approvals workspace (`/app/cases`): deterministic `Decimal` comparison grid, currency/tax/pack assumptions, explicit missing prices, and version-bound approval records.
6. [P12-T06](../tasks/p12-t06.md) — Shared right-hand evidence & context drawer (`context-panel`) and bidirectional context handoff between `/app/chat` and domain workspaces.

**Continuity and boundaries:**
- P12 does not block closure of the social milestone in [P8](p8-first-value-ui.md). Once the owner selects a concrete non-social connector pilot, [P8-T34](../tasks/p8-t34.md) transfers into P12 alongside P12-T02.
- Every section lands behind `NavItem(..., ready=False)` until its underlying service, permission catalog entry, and acceptance checks are verified.
