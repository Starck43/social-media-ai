# P8 — First value and support UI

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p8) · [full historical notes](../audit/phase-p8-history.md)

**Goal:** A first-time owner reaches value and support can diagnose what happened.

The universal-task product direction is chat-first across the complete user
journey, with directly accessible pages for source, scenario, task, schedule and
digest management. The current code foundation and the remaining acceptance
work are separate facts; see [P8-T29 history](../audit/p8-t29-history.md).

**Plan — universal social milestone (ordered):**

1. [P8-T29](../tasks/p8-t29.md) — shared task foundation and bounded VK collection;
   its open method/profile evidence gate is recorded in Q-5 and its own card.
2. [P8-T36](../tasks/p8-t36.md) — visible VK connection/access capabilities.
   Mocked work can proceed while Q-5 is open; live capability claims cannot.
3. [P8-T37](../tasks/p8-t37.md) — primary chat lifecycle and independently usable
   source/scenario/task/schedule/digest management journeys. It may complete
   before composition in T38.
4. [P8-T38](../tasks/p8-t38.md) — compose collect, analyze and digest generation
   for one-off and scheduled runs, reusing existing jobs and digest services.
5. [P8-T39](../tasks/p8-t39.md) — accept one bounded, evidence-linked
   VK research-to-digest journey at the exact integrated revision.
6. [P8-T33](../tasks/p8-t33.md) — optional extension beyond the minimum pilot:
   select a bounded set of verified VK result categories, with explicit cards
   for any other requested categories before T33 closes.

T36 and T37 can proceed from the integrated T29 foundation without waiting for
the full T29 live acceptance gate. T38 can build against mocked data from the
integrated foundation; it depends on T37, uses existing payer/quota controls,
and applies P2-T15 entitlements if that separate work is available. T37 does
not depend on T38. T39's live method evidence waits on Q-5. T39 is the integrated
journey gate, not a way to rename or retroactively accept P8-T29.

**Other active P8 cards:**

- [P8-T10](../tasks/p8-t10.md) — runtime freshness identity
- [P8-T11](../tasks/p8-t11.md) — incomplete-delivery aggregate signal
- [P8-T13](../tasks/p8-t13.md) — named support/escalation + pilot measures
- [P8-T15](../tasks/p8-t15.md) — Admin API connectivity, mock and real checks
- [P8-T17](../tasks/p8-t17.md) — Model response diagnostics and consent
- [P8-T18](../tasks/p8-t18.md) — Model tests in single-owner mode
- [P8-T20](../tasks/p8-t20.md) — Provider cost evidence and diagnostic controls

**Deferred beyond the social milestone:** [P8-T34](../tasks/p8-t34.md) keeps the
future non-social connector direction. It is not a current P8 social release
gate. P8 cannot be marked fully closed while this deferred item remains attached
to P8; first move it to a future phase or record an explicit owner cancellation.

- [P8-T36](../tasks/p8-t36.md) — Reusable horizontal relationship picker

- [P8-T37](../tasks/p8-t37.md) — Task plan execution scope and shared mutation policy; [PR #245](https://github.com/Starck43/social-media-ai/pull/245), independent local acceptance pending

**Accepted history:**

- [P8-T35](../audit/p8-t35.md) — reconcile the universal social task plan and follow-up cards · 2026-10-26 · `a0cda00`
- [P8-T26](../audit/phase-p8-history.md#p8-t26) — implement the accepted jobs lifecycle audit: built-in cleanup independent of a scheduled prune task, default 24-hour terminal…
- [P8-T01](../audit/phase-p8-history.md#p8-t01) — home mixed latest-results feed · 2026-10-16 · `19ce6e1`
- [P8-T02](../audit/phase-p8-history.md#p8-t02) — home upcoming schedules · 2026-10-16 · `19ce6e1`
- [P8-T03](../audit/phase-p8-history.md#p8-t03) — server-side user notification preferences · 2026-10-16 · `19ce6e1`
- [P8-T04](../audit/phase-p8-history.md#p8-t04) — initialize `source_ids` in the add-task form model · 2026-10-16
- [P8-T05](../audit/phase-p8-history.md#p8-t05) — verify reported mobile page-width overflow · 2026-10-16 · `9a7c5cd`
- [P8-T06](../audit/phase-p8-history.md#p8-t06) — inventory operational signal and readiness coverage against this phase's gate · 2026-10-16 · `431059c`
- [P8-T07](../audit/phase-p8-history.md#p8-t07) — stale-source observable · 2026-10-24 · `f762088`
- [P8-T08](../audit/phase-p8-history.md#p8-t08) — credential expiry/near-expiry signal · 2026-10-25
- [P8-T09](../audit/phase-p8-history.md#p8-t09) — queue backlog/age/quarantine signal · 2026-10-25 · `2d15b4e`
- [P8-T12](../audit/phase-p8-history.md#p8-t12) — unknown-price/spend exposure signal · 2026-10-24 · `f762088`
- [P8-T14](../audit/phase-p8-history.md#p8-t14) — integrate the shared local LLM check/admin UI with personal billing · 2026-10-17 · `c18ee2f`
- [P8-T16](../audit/phase-p8-history.md#p8-t16) — repair admin sidebar/menu continuity, display the verified actor and platform role, and expose platform-admin-only… · 2026-10-24 · `7509f8f`
- [P8-T19](../audit/phase-p8-history.md#p8-t19) — audit collect force-refresh semantics and task-run feedback · 2026-10-17
- [P8-T21](../audit/phase-p8-history.md#p8-t21) — add/edit related FK/m2m records without losing the parent form.
- [P8-T22](../audit/phase-p8-history.md#p8-t22) — audit and simplify the social analysis web journey
- [P8-T23](../audit/phase-p8-history.md#p8-t23) — compact inline source picker on the task edit page · 2026-10-19
- [P8-T24](../audit/phase-p8-history.md#p8-t24) — keep incomplete-analysis recovery and its progress on analysis detail · 2026-10-19 · `ddc9b99`
- [P8-T25](../audit/phase-p8-history.md#p8-t25) — settings information layout and compact member access
- [P8-T27](../audit/phase-p8-history.md#p8-t27) — implement the Platforms settings tab for active platform admins, correct admin button contrast and bulk-delete confirmation…
- [P8-T28](../audit/phase-p8-history.md#p8-t28) — implement the owner-approved two-stage task run progress
- [P8-T30](../audit/p8-t30.md) — read-only scoped reverse-FK sections on console object pages · 2026-10-25 · `fc0a4a1`
- [P8-T31](../audit/p8-t31.md) — configurable paginated admin reverse-FK inlines · 2026-10-25 · `008df07`

- [P8-T32](../audit/p8-t32.md) — collapsed related rows and permission-aware links; accepted; date not recorded; source `9534f9a`.

**Phase status:** Active phase; close only after active plan items are accepted
and the deferred P8-T34 item has been transferred to a future phase or explicitly
cancelled by the owner. Existing diagnostic/support tasks remain independent.
