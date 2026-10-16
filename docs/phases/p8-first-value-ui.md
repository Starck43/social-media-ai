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

- [x] P8-T01 — home mixed latest-results feed; accept: permitted analyses and
  digest runs merged into ≤5 cards via a bounded read-time projection;
  analysis creation time, digest period and actual delivery state labelled
  separately; no fabricated success, duplicated delivery or extra LLM call;
  data loading and rendering gated by effective VIEW rights (`aianalytics.view`,
  `source.view`), and digest-run reads/rendering additionally gated on
  independent `digestrun.view` rights; depends: none; owns: home
  template/route/tests; owner: home-agent; status: DONE — accepted: codex,
  2026-10-16, source `19ce6e1`; 20 focused unit tests, SQL-shape check, and
  15 PostgreSQL/ASGI acceptance cases passed.

- [x] P8-T02 — home upcoming schedules; accept: ≤3 actual upcoming, effectively
  active schedules ordered by `next_run_at`; missing next time, paused and
  completed one-shot tasks handled honestly; recent-by-creation list never
  presented as schedules; no multi-step workflow claims; depends: none;
  owns: home template/route/tests; owner: home-agent; status: DONE — accepted:
  codex, 2026-10-16, source `19ce6e1`; same focused and PostgreSQL/ASGI checks
  as P8-T01.

- [x] P8-T03 — server-side user notification preferences; accept: server is the
  source of truth, browser values import only on explicit user action, default
  flags and device-local quiet hours retained, real
  SQLAdmin/ASGI/PostgreSQL role, revocation and disable race/rollback
  acceptance for C/D executed via R10; depends: none; owns: shell/settings lane;
  owner: prefs-agent; status: DONE — accepted: codex, 2026-10-16, source
  `19ce6e1`. No-DB pytest 77/77, preference client 17/17, dashboard browser
  8/8; PostgreSQL/ASGI notification, SQLAdmin role/revocation and workspace
  mutation/race/rollback acceptance 10/10 PASS. Owner-authorized coordinator
  fallback ran the same acceptance after OpenCode/Kilo were unavailable.
  Alembic 0103 is current in both `public` and `test_schema`.

- [x] P8-T04 — initialize `source_ids` in the add-task form model;
  accept: opening the form has no `addTaskForm().task.source_ids.includes`
  error, selected IDs round-trip on selection/reset, submit semantics unchanged,
  verified with real Alpine; depends: none; owns: add-task form JS/tests;
  owner: codex; status: DONE — accepted: codex, 2026-10-16, `9a7c5cd`;
  real Alpine/Chromium selection/reactive/POST and negative-control PASS.

- [x] P8-T05 — verify reported mobile page-width overflow; accept: inspect
  authenticated ASGI pages at 375px and 320px with Chromium, confirm document
  width and dialog behavior; change CSS only if overflow reproduces. No current
  overflow reproduced; internal table scrolling is contained. Dialog focus,
  Escape and one accepted POST pass. No CSS change; accepted: codex,
  2026-10-16, source `9a7c5cd`; prior report:
  [PR #175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703)

- [x] P8-T06 — inventory operational signal and readiness coverage against
  PRD-08; accept: no duplicate P1/P2 work; depends: none;
  owns: inventory lane; owner: opencode; status: DONE — accepted: codex,
  2026-10-16, source `431059c`; coverage and follow-ups below.

Coverage evidence vs PRD-08 (state/refs → task):
- Stale sources MISSING (T07): `last_checked`/`get_sources_for_monitoring(hours_since_check=)` gate runtime; bell only `source_inactive`.
- Credential expiry PARTIAL (T08): `connections.py` reauth/missing + human expiry text; no near-expiry signal.
- Queue backlog/age/quarantine MISSING (T09): `job_manager.reap_stale(30)` quarantines + retries; no operator signal.
- Runtime freshness MISSING (T10): `health.py` `/livez`+`/readyz` = API+DB `Permission` only, not worker.
- Incomplete delivery MISSING (T11): receipts + quarantine + retry UI; bell coalesces `api_error`; no aggregate.
- Unknown price/spend DEPENDS (T12): `budget_admission` UNDEFINED + spend managers; P2 scope on P2-T06.
- Support/escalation + measures MISSING (T13): none named in P8 files.
- Delivered bell: `task_failures`, `statuses_needing_action`, `report_ready`/`api_error`/`connection_error`/`source_inactive`, read/read-window — windowed 100/50, dedup, permission-gated.

- [ ] P8-T07 — stale-source observable; accept: active sources not checked within
  configured hours via `get_sources_for_monitoring`/`last_checked`, honest "never checked",
  gated `source.view`; depends: none; owns: source freshness lane; owner: unassigned; status: TODO
- [ ] P8-T08 — credential expiry/near-expiry signal; accept: reauth/missing plus near-expiry from
  vault `expires_at` metadata — reads only, never decrypts; `renewable` auto-renewal is not reauth/error;
  personal vs operator split honest; depends: none; owns: credential signal lane; owner: unassigned; status: TODO
- [ ] P8-T09 — queue backlog/age/quarantine signal; accept: operator view reusing
  `job_manager` claim/reap evidence (stale quarantine, retry receipts): pending count, oldest age, quarantined
  rows; no runtime write; depends: none; owns: queue signal lane; owner: unassigned; status: TODO
- [ ] P8-T10 — runtime freshness identity; accept: signal distinct from `/livez`-`/readyz` (API+DB only),
  honestly reporting single `app.runtime` component presence; depends: none;
  owns: runtime freshness lane; owner: unassigned; status: TODO
- [ ] P8-T11 — incomplete-delivery aggregate signal; accept: bell/operator view of undelivered/retried/
  quarantined deliveries from existing outcome receipts (job + digest) only, no new delivery semantics;
  depends: none; owns: delivery signal lane; owner: unassigned; status: TODO
- [ ] P8-T12 — unknown-price/spend exposure signal; accept: operator view of provider models
  without known price plus spend totals via personal account managers (post P2-T06); depends: P2-T06;
  owns: financial visibility lane; owner: unassigned; status: TODO
- [ ] P8-T13 — named support/escalation + pilot measures; accept: escalation owner and agreed
  latency/freshness/usefulness measures for the managed pilot, written and owner-signed; depends:
  P8-T10, P8-T11; owns: support contract lane; owner: unassigned; status: TODO

- [ ] P8-T14 — integrate the shared local LLM check/admin UI with personal billing;
  accept: owner-reviewed combined candidate preserves CONFIGURE/CSRF/paid consent,
  trusted initiating-user payer, explicit workspace, fixed 8-token/20-second probe,
  unknown-usage semantics and no automatic paid retry; short/mock remain local;
  depends: P2 billing review and separately authorized local acceptance;
  owns: isolated integration branch only; source lanes remain unchanged;
  owner: remote Notion agent (`review/admin-llm-billing-integration`);
  status: IN_PROGRESS — code-review-only candidate from pinned source lanes;
  owner relayed 40-case acceptance at `4f290aee` with two failures;
  corrected model-ID overflow guard; updated suite declares 41 cases, not rerun;
  see `docs/validation/admin-llm-billing-acceptance.md`; VIEW-only catalog gate
  remains explicit and shared grants/financial evidence must not be rewritten;
  historical P8-T06 in the admin UI lane collided with the accepted inventory ID;
  that inventory and all other lane evidence are preserved, not re-accepted here.
  No DB/runtime/provider execution or dev merge is authorized for this candidate.

Historical home placeholders (attention block, empty/access states, extension
points) are covered by merged B/D slices; future kinds enter attention only
when their contracts exist — no fake rows or Soon cards. NULL-role membership
labels are DELIVERED (`2dd3161`).

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
