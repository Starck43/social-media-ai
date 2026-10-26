# P10 — Source-audit hardening

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p10) · [full historical notes](../audit/phase-p10-history.md)

**Goal:** Verified correctness and security findings are closed with bounded evidence.

**Plan — active task cards:**

- [P10-T08](../tasks/p10-t08.md) — Home pipeline permission gates
- [P10-T09](../tasks/p10-t09.md) — Truthful pipeline counts/labels
- [P10-T16](../tasks/p10-t16.md) — Scheduling model layout checks
- [P10-T19](../tasks/p10-t19.md) — Reconcile the failing security suites with the current authorization model
- [P10-T22](../tasks/p10-t22.md) — Reconcile the dispatch-identity suite with the tariff receipt contract


- [P10-T24](../audit/p10-t24.md) — portable handoff, owner decision before integration and migration evidence review; accepted 2026-10-26 at `1df675f`.

- [P10-T25](../tasks/p10-t25.md) — Unused utility cleanup and slug transliteration; [PR #243](https://github.com/Starck43/social-media-ai/pull/243), historical conflicting branch ID P10-T23 reconciled
- [P10-T26](../tasks/p10-t26.md) — notification/bot-action/agent-session model and manager layout
- [P10-T27](../tasks/p10-t27.md) — legacy notification API correctness
- [P10-T28](../tasks/p10-t28.md) — retire root-level model compatibility modules

**P10-T26 audit follow-up order (within this audit, not a reprioritization of
other lanes):** validate/integrate P10-T26 first; then P10-T27 API correctness.
P10-T28 is lower-priority mechanical cleanup, one domain at a time. Product
redesigns belong to [P4-T02](../tasks/p4-t02.md) and [P9-T08](../tasks/p9-t08.md);
their proposals are not approved or shipped features.

**Accepted history:**

- [P10-T17](../audit/p10-t17.md) — compact agent entry/workflow and lazy task documentation · 2026-10-25 · `b8dac60`
- [P10-T02](../audit/phase-p10-history.md#p10-t02) — remove password hashes from HTTP user responses
- [P10-T03](../audit/p10-t03.md) — separate self-service profile updates from privilege updates · 2026-10-25 · `bd97589`
- [P10-T05](../audit/phase-p10-history.md#p10-t05) — align legacy API DB probes with bounded redacted readiness · 2026-10-22 · `ab532db`
- [P10-T10](../audit/phase-p10-history.md#p10-t10) — distributable package runtime completeness
- [P10-T13](../audit/phase-p10-history.md#p10-t13) — repair post-migration LLM CLI contracts · 2026-10-24 · `0693c69`
- [P10-T11](../audit/phase-p10-history.md#p10-t11) — audit post-migration CLI/API and embedded web-chat tools · 2026-10-24 · `94e9fff`
- [P10-T12](../audit/phase-p10-history.md#p10-t12) — preserve chat task configuration on partial updates and restore task listing · 2026-10-24 · `6c581bf`
- [P10-T15](../audit/phase-p10-history.md#p10-t15) — report scenario resets and warnings on default-model deletion · 2026-10-24 · `cb2793a`
- [P10-T01](../audit/p10-t01.md) — replace unauthenticated password mutation with authorized recovery · 2026-10-25 · `526cfd2`
- [P10-T04](../audit/p10-t04.md) — shared, cookie-independent login protection · 2026-10-25 · `526cfd2`
- [P10-T18](../audit/p10-t18.md) — repair the workspace-access contracts broken by the authorization split · 2026-10-25 · `b3c295c`
- [P10-T06](../audit/p10-t06.md) — safe provider error envelopes with preserved accounting · 2026-10-25 · `fc0a4a1`
- [P10-T07](../audit/p10-t07.md) — one digest scope, window and base-row set for every report section · 2026-10-25 · `fc0a4a1`
- [P10-T21](../audit/p10-t21.md) — admin polish after P8-T30
- [P10-T20](../audit/p10-t20.md) — align the digest-confirmation suites with the deliberate intent dedup · 2026-10-26 · `6e6dbbf`

- [P10-T23](../audit/p10-t23.md) — compact documentation hierarchy and synchronized lifecycle · 2026-10-26 · `e32871e`

**Phase status:** Active phase; close only after every plan item is accepted.
