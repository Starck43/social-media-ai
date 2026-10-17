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

- [x] P8-T14 — integrate the shared local LLM check/admin UI with personal billing;
  accept: owner-reviewed combined candidate preserves CONFIGURE/CSRF/paid consent,
  trusted initiating-user payer, explicit workspace, fixed 8-token/20-second probe,
  unknown-usage semantics and no automatic paid retry; short/mock remain local;
  depends: P2 billing review and separately authorized local acceptance;
  owns: isolated integration branch only; source lanes remain unchanged;
  owner: remote Notion agent (`review/admin-llm-billing-integration`);
  status: DONE — accepted: coordinator review in OpenCode session under explicit
  owner instruction, 2026-10-17, at dev `c18ee2f`. Closeout review re-verified:
  PR #205 state MERGED at `b0bd202b`, tested HEAD `0b1fd71d` and merge trees are
  byte-identical (`d9e1a3c9a331e9d06d6ce22d91b39b39c6fbf48d` both); PR #206
  (`c27b9468`) is a docs-only two-file evidence sync (ROADMAP.md + this file);
  GitHub confirms both merges. All evidence caveats below are preserved as
  accepted limitations: warning details of the 41-case local run were not
  supplied, Mac-local audit files were not independently inspected remotely,
  a genuine VIEW-only DB login was not tested, and the 56-case offline browser
  QA belongs to `ace7c536`, not the final HEAD.
  [PR #205](https://github.com/Starck43/social-media-ai/pull/205) merged into dev
  with explicit owner approval; full tested/merge SHAs: HEAD
  `0b1fd71d08a332e668d25eeb585fc27bb01127af`, merge commit
  `b0bd202bccb04cdbcad009ddd6a8bd5664221a8c`.
  Evidence at the tested HEAD: 110/110 remote isolated tests; owner-relayed
  local `tests/test_admin_model_billing_acceptance.py`: 41 collected / 41 passed,
  0 failed/skipped/deselected/errors, collection/execution exits 0;
  execution reported 12 warnings in 62.08s (warning details not supplied).
  Mac-local audit files were not independently inspected remotely.
  `OBSERVED_OPERATOR_MODEL_GRANTS []`; actual DB-profile enforcement passed.
  A genuine VIEW-only DB login was not tested; VIEW/UPDATE do not imply
  CONFIGURE in separate controlled isolated tests. The owner explicitly
  accepted this evidence split. Historical 56-case offline browser QA belongs
  to `ace7c536300ae3ec1d5f9abaaa430bec4cdb74ad`, not a rerun at the final HEAD.
  See PR #205 and `docs/validation/admin-llm-billing-acceptance.md` for scope.
  Source PRs #200/#201 are snapshot-integrated, not ancestry-merged; closeout
  content review found no outstanding source changes requiring another merge.
  Their branches and owner-local artifacts remain preserved; closing these PRs
  requires separate owner authorization. The historical admin P8-T06 collision
  remains mapped to T14; accepted inventory T06 is unchanged.
  DONE marking and ROADMAP-row removal were reserved to the coordinating Codex
  after exact-SHA review; executed here by the owner's explicit in-session
  instruction with the same exact-SHA review recorded above. This is not broader
  P2/billing acceptance or P8 phase approval. No repeated tests, schema/grant/
  .env operations, fixture cleanup, runtime activation, deployment or live/paid
  calls are authorized by closeout.

Historical home placeholders (attention block, empty/access states, extension
points) are covered by merged B/D slices; future kinds enter attention only
when their contracts exist — no fake rows or Soon cards. NULL-role membership
labels are DELIVERED (`2dd3161`).

## Analysis progress delivery

PR #203 adds persisted source analysis progress to the source table and
"Выполнить сейчас" dialog, with role-gated diagnostics. Acceptance at
`dd36e7b` and Safari check at `ca282f7`: noDB 222 passed, DB/ASGI 33 passed;
authenticated Safari view and progress endpoint returned HTTP 200. No
migrations or dependency changes. This delivery is separate from inventory
P8-T06 and does not alter its task record.

- [ ] P8-T15 — distinct admin model checks; accept: short performs an authenticated
  metadata API request without generation; mock remains offline; real forwards
  the supplied prompt or a short default, with bounded output, existing consent,
  payer admission and no retry; mode-specific useful diagnostics; no migrations;
  owns: admin model action/endpoint/template/JS, probe helpers, focused tests/docs;
  owner: remote Notion agent (`fix/admin-model-real-request`); status: BLOCKED
  (implementation ready; required local ASGI/PostgreSQL acceptance unavailable).
  Remote evidence: 107 isolated no-DB tests passed; 56 offline Chromium
  viewport/theme/state cases plus form/consent/no-JS checks passed;
  Black/isort checks, py_compile and diff whitespace checks passed.
  No migration, real provider or owner-local application run. Updated local
  acceptance covers metadata vs offline web checks and blank/custom prompt
  settlement. Keep Draft pending the exact-SHA local R8/R10 run.

- [ ] P8-T16 — repair admin sidebar/menu continuity, display the verified actor and
  platform role, and expose platform-admin-only operator grant/permission-label
  editing without mutable catalog identities or role escalation; owner: remote
  Notion agent (`fix/admin-shell-and-role-management`); status: BLOCKED
  (remote implementation checked; real authenticated PostgreSQL acceptance pending).
  Owns: shared admin shell/CSS/authorization, role and permission views, focused
  regression tests and documentation. P8-T15 model request behavior is unchanged.
  Evidence: 131 isolated tests passed, including real SQLAdmin routes/forms with
  explicit identity/storage doubles; 18 offline Chromium layout cases and one
  no-JavaScript case passed. This is not real DB/owner-account acceptance.
  Before merge: sequential local R8/R10 verification at the published PR SHA of
  admin/operator/revoked identities, menu continuity after model-test POST,
  persisted operator grants and permission names with protected catalog fields
  unchanged. Preserve existing grants/data; no reset, migration or live API calls.
  Additional synthetic grant-selection cases were blocked by the execution
  safety gate and were not applied or run; the blocker was not bypassed.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
