# P8 — First value and support UI (gate PRD-08)

Goal: operators can detect and own stale sources, backlog, stalled loops,
delivery failures or spend anomalies; home answers "what needs attention, what
was produced, what happens next".
Design contracts: [assistant-shell.md](../design/assistant-shell.md),
[NOTIFICATIONS.md](../NOTIFICATIONS.md),
[TENANCY.md](../TENANCY.md#web-settings-and-ownedgranted-workspace-context).
Delivered: home cleanup (PR #185, owner-local acceptance at `01f719d` in
IMPLEMENTATION_STATUS); shell A (#188), unified attention B (#189),
browser-local notification preferences C (#190), bounded settings scopes D
(#191). Stack stays FastAPI/Jinja/Alpine/HTMX with existing managers and shared
authorization predicates; no SPA replacement, no new navigation service.
Attention feed lives in the floating bell (B), not a home card.

## Tasks

- [ ] P8-T01 — home mixed latest-results feed; accept: permitted analyses and
  digest runs merged into ≤5 cards via a bounded read-time projection;
  analysis creation time, digest period and actual delivery state labelled
  separately; no fabricated success, duplicated delivery or extra LLM call;
  data loading and rendering gated by effective VIEW rights (`aianalytics.view`,
  `source.view`); depends: none; owns: home template/route/tests;
  owner: unassigned; status: TODO

- [ ] P8-T02 — home upcoming schedules; accept: ≤3 actual upcoming, effectively
  active schedules ordered by `next_run_at`; missing next time, paused and
  completed one-shot tasks handled honestly; recent-by-creation list never
  presented as schedules; no multi-step workflow claims; depends: none;
  owns: home template/route/tests; owner: unassigned; status: TODO

- [ ] P8-T03 — assistant shell closeout; accept: durable notification-preference
  storage decided (C is browser-local only) and implemented if approved; real
  SQLAdmin/ASGI/PostgreSQL role, revocation and disable race/rollback acceptance
  for C/D executed via R10; settings lazy-load follow-ups resolved;
  depends: owner decision on durable storage; owns: shell/settings lane;
  owner: remote Notion agent; status: TODO — real acceptance deferred by owner

- [ ] P8-T04 — initialize `source_ids` in the add-task form model;
  accept: opening the form has no `addTaskForm().task.source_ids.includes`
  error, selected IDs round-trip on selection/reset, submit semantics unchanged,
  verified with real Alpine; depends: none; owns: add-task form JS/tests;
  owner: unassigned; status: TODO — evidence:
  [PR #178 runtime report](https://github.com/Starck43/social-media-ai/pull/178#issuecomment-6154504317)

- [ ] P8-T05 — fix mobile page-width overflow; accept: affected pages fit a
  verified 375px viewport without unintended horizontal scrolling
  (observed document width 432px), confirmation dialog focus/keyboard behavior
  retained, overflowing base-layout element identified independently of the
  Chromium emulation discrepancy; depends: none; owns: base layout CSS/tests;
  owner: unassigned; status: TODO — evidence:
  [PR #175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703)

- [ ] P8-T06 — truthful analysis progress in the source detail "Последние
  анализы" table and task "Выполнить сейчас" modal; accept: identify
  authoritative persisted progress/state and its data limitations; UI shows a
  simple running/stopped state and percentage of raw data processed to ordinary
  users, handling unknown/stale/failed states honestly; additional privacy-safe
  failure diagnostics exposed only to administrator/superuser; both requested
  views updated; workspace authorization preserved; focused checks assigned
  separately through test-runner; no unrelated UI overhaul; depends: none;
  owns: source detail/task modal incl. progress + diagnostics; owner:
  codex-analysis-progress; status: IN_PROGRESS — evidence:
  [Draft PR #203](https://github.com/Starck43/social-media-ai/pull/203)

Historical home placeholders (attention block, empty/access states, extension
points) are covered by merged B/D slices; future kinds enter attention only
when their contracts exist — no fake rows or Soon cards. NULL-role membership
labels are DELIVERED (`2dd3161`).

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
