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
2. [P8-T38](../audit/p8-t38.md) — DONE 2026-10-27: selected VK connections, fresh request access checks and safe catalogs; accepted `c0aa1c9de18962b6fe86405daedbdb4bf5a01bda`.
   The isolated selection-policy increment was pre-integrated into dev on
   2026-10-27 (PR #258, merge `5848c216`) with acceptance deferred — its six
   remaining work items are listed in the
   [audit note](../audit/p8-t38-integration.md).
   Mocked work can proceed while Q-5 is open; live capability claims cannot.
3. [P8-T39](../audit/p8-t39.md) — DONE 2026-10-28: chat collect/analyze lifecycle,
   saved-plan follow-up and direct management; accepted
   `eef36492ea7bed5a683806c5d9cbbc7985fa8d0c`, PR #270.
4. [P8-T40](../audit/p8-t40.md) — DONE 2026-10-28: composed collect/analyze/digest
   runs and schedules, separate report windows and confirmed frozen-report delivery;
   accepted `f66795cf0242200a53853bb2b1e9b01da595394d`, PR #271.
5. [P8-T41](../tasks/p8-t41.md) — accept one bounded, evidence-linked
   VK research-to-digest journey at the exact integrated revision.
6. [P8-T33](../tasks/p8-t33.md) — optional extension beyond the minimum pilot:
   select a bounded set of verified VK result categories, with explicit cards
   for any other requested categories before T33 closes.

T38 can proceed from the integrated T29 foundation without waiting for the
live VK gate. T39 reuses the accepted shared execution-scope/mutation policy in
P8-T37. T40 depends on T39, uses
existing payer/quota controls, applies P2-T15 entitlements if available, and
can build against mocked VK data while Q-5 is open. T41's live method evidence
waits on Q-5. T41 is the integrated
journey gate, not a way to rename or retroactively accept P8-T29.

7. [P8-T42](../audit/p8-t42.md) — DONE 2026-10-31: contextual chat actions,
   permission-filtered task plan/results, embedded pages and Safari UX refinements;
   accepted `6a2e6de55ae417b6ec7239ad00103cba8335f161`, PR #290
   (merge `27f83079df7488bec6c4deaf780a1032477cab47`).
8. [P8-T43](../tasks/p8-t43.md) — preserve each source post permalink and its
   media URL references from collection through analysis and agent-rendered results;
   use source URLs only (no media downloading/storage). Coordinate overlapping chat
   presentation with P8-T42.

9. [P8-T44](../audit/p8-t44.md) — DONE 2026-10-31: private photo/document attachments in the
   shared chat with magic-byte validation, large-file token confirmation gate and model capability checks; accepted `e1f79788`, PR #292 (merge `7c76bace940c174a2cd33d77eaf839bc9f7d4a61`).

10. [P8-T45](../audit/p8-t45.md) — DONE 2026-10-31: shared compact model probes, invocation counters and safe quota/budget/price warnings; accepted `3d69c36a223b2023ae32fa4d1b59a9bd9d755296`, PR #291 (merge `57b5bebc2f1759d2d8543576a8c842c3d17f7acf`).

**Other active P8 cards:**

- [P8-T10](../audit/p8-t10.md) — runtime freshness identity; accepted 2026-10-27, integrated in PR #248 (merge `d7213c6b`)
- [P8-T11](../audit/p8-t11.md) — incomplete-delivery aggregate signal; accepted 2026-10-27, integrated in PR #252 (merge `83c1f79a`)
- [P8-T13](../audit/p8-t13.md) — named support/escalation + pilot measures; accepted 2026-10-27, integrated in PR #260 (merge `5f1066b5`)
- [P8-T15](../audit/p8-t15.md) — Admin API connectivity, mock and real checks; accepted 2026-10-28, evidence commit `738d14dc`
- [P8-T17](../audit/p8-t17.md) — Model response diagnostics and consent; accepted 2026-10-28, evidence commit `738d14dc`
- [P8-T18](../audit/p8-t18.md) — Model tests in single-owner mode; accepted 2026-10-28, integrated in dev (cherry-pick `4616c583`)
- [P8-T20](../audit/p8-t20.md) — Provider cost evidence and diagnostic controls; accepted 2026-10-28, integrated in dev (cherry-pick `4d3ce50e`)
- [P8-T36](../audit/p8-t36.md) — reusable horizontal relationship picker; accepted 2026-10-27, integrated in PR #244 (merge `682dbe37`)
**Deferred beyond the social milestone:** [P8-T34](../tasks/p8-t34.md) keeps the
future non-social connector direction. It is not a current P8 social release
gate. P8 cannot be marked fully closed while this deferred item remains attached
to P8; first move it to a future phase or record an explicit owner cancellation.

**Accepted history:**

- [P8-T37](../audit/p8-t37.md) — task plan execution scope and shared mutation policy · 2026-10-26 · `f695e64`
- [P8-T35](../audit/p8-t35.md) — reconcile the universal social task plan and follow-up cards · 2026-10-26 · `678466b`
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
