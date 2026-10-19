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
  settlement. PR #207 was merged at owner-authorized exception; its local
  R8/R10 acceptance remains deferred, not passed.

- [ ] P8-T16 — repair admin sidebar/menu continuity, display the verified actor and
  platform role, and expose platform-admin-only role-grant/permission-label
  editing without mutable catalog identities or role escalation; owner: remote
  Notion agent (`fix/admin-role-picker-controls`); status: BLOCKED
  (owner-requested self-role protection and responsive permission transfer picker;
  base dev `58a90c1` (includes PR #212 and fresh documentation); owner-local real PostgreSQL acceptance remains pending).
  Owns: shared admin shell/CSS/authorization, role and permission views, focused
  regression tests and documentation. P8-T15 model request behavior is unchanged.
  Evidence: 131 isolated tests passed, including real SQLAdmin routes/forms with
  explicit identity/storage doubles; 18 offline Chromium layout cases and one
  no-JavaScript case passed. This is not real DB/owner-account acceptance.
  The owner explicitly authorized merging PR #208 with real-environment
  verification deferred to their machine; this does not certify DB acceptance
  or mark the task DONE. Pending sequential local R8/R10 verification: admin/
  operator/revoked identities, menu continuity after model-test POST, persisted
  operator grants and permission names with protected catalog fields unchanged.
  Preserve existing grants/data; no reset, migration or live API calls.
  Historical PR #208 limitation: additional synthetic grant-selection cases were
  blocked by the execution safety gate and were not applied or run in that PR.
  Owner-requested follow-up from dev `b70911d`: removed the operator-only role-row
  edit restriction in both SQLAdmin and the canonical manager writer. An active
  platform admin may edit stored grants on user/admin/operator records; other
  actors remain denied and fixed role authority/catalog identities are unchanged.
  Follow-up evidence: 155 isolated tests passed, including all three edit links,
  GET/POST forms with explicit storage doubles, manager policy for every role,
  revocation/inactive/anonymous denial, CSRF and immutable-field checks.
  Real database persistence is not certified; the owner's local-verification
  agreement remains in force. No migration or runtime/provider operation.

  Follow-up from dev `a36ef4f`: self-role assignments now fail before any manager
  write (including `admin` to `admin`), and generic user CRUD rejects role fields.
  Another active platform admin can still assign roles to other accounts.
  Role grants use a dependency-free Available/Chosen picker with search, counts,
  double-click, touch buttons, keyboard support, native select submission/reset
  and bounded list-only scrolling. Native no-JS selection is preserved.
  Evidence on refreshed dev `3e57794`: 192 isolated tests passed (0 failed/skipped);
  18 offline Chromium
  width/theme/state cases plus desktop, touch and no-JS interaction suites passed
  with 30 synthetic permissions (Chromium 153.0.8010.12). Static formatting,
  compilation, JS syntax and whitespace checks passed. No app/runtime, DB,
  migration, live provider or deployment operation. Owner-local sequential
  verification still needs persisted role grant edits, forbidden self-role API
  writes, allowed other-account assignment and the actual owner browser form.
  The standing owner request to publish to dev and test locally remains in force;
  this is a delivery handoff, not final task/phase acceptance.

  Styling follow-up from dev `bd67996` (refreshed to docs-only `58a90c1`): neutralized the legacy checkbox-pill
  cascade with block-flow lists, compact borderless/gapless rows and top-aligned
  empty states. A viewport-height role editor keeps ordinary SQLAdmin footer
  buttons visible; no full-width custom button styling. Only permission lists
  scroll. The canonical Permission manager supplies code/display-name choices;
  domain filtering, resource headings and word search work without changing the
  hidden native select or server authorization/self-role protection.
  Evidence: 204 isolated tests passed (0 failed/skipped); 48 offline Chromium
  cases (390x667, 390x844, 768x700, 1440x900; both themes; six selection states)
  plus desktop, group/name-filter, touch and no-JS interaction suites passed with
  up to 206 synthetic permissions. Checks assert footer/viewport bounds, row CSS,
  legacy selector collision, search/group selection retention, native FormData,
  reset and contained scrolling. Black/isort, compilation, JS syntax and diff
  checks passed. Real owner-browser/DB acceptance remains deferred under the
  standing owner delivery/local-feedback instruction. No migration, runtime,
  provider call or deployment; task is not self-accepted as DONE.

  Presentation-only follow-up from fresh dev `9868b20`: field caption and a smaller
  hint below the panes, circular transfer arrows with marked-item counts between
  them, and non-collapsible access rules with readable spacing. Footer buttons,
  grouping/search, row styles and authorization/persistence behavior are unchanged.
  Evidence: 59 isolated catalog/role/self-role tests and 48 offline Chromium cases
  plus four interaction suites passed with up to 206 synthetic permissions.
  Checks include caption/button positions, 44px circular targets, badge counts,
  always-visible rules, small hint text, keyboard/touch, native submission/reset,
  filtering and viewport/footer bounds. Formatting, JS syntax and diff checks
  passed. Remote mocks only; real owner-browser/DB acceptance is still deferred.
  Shared docs synchronized with dev; foreign task records and evidence preserved.

- [ ] P8-T17 — remove redundant paid checkbox; explicit real POST is consent;
  readable answer plus sanitized provider JSON and transport/accounting statuses;
  visible pre-dispatch blockers with no bypass of CONFIGURE/CSRF/payer admission;
  owns: model-test action/endpoint/template/JS/CSS, opt-in client diagnostics,
  focused regression tests/docs; owner: remote Notion agent
  (`fix/admin-model-diagnostics`); status: BLOCKED (implementation ready; real
  PostgreSQL/owner-environment acceptance deferred under the owner's explicit
  implementation-and-dev instruction following their merge exception).
  Evidence: 167 isolated tests passed (actual client source over MockTransport,
  explicit storage/admission doubles; not real billing integration); 72 offline
  Chromium viewport/theme/state cases plus blank/custom submit, no extra consent,
  JSON copying/manual fallback, result focus, no-JS, keyboard and busy guard
  passed. Black/isort scoped checks, py_compile and diff whitespace check passed.
  Local acceptance was updated but not run: real payer/ledger/settlement, CSRF,
  revocation and workspace guards remain required R8/R10 follow-up. No migration,
  schema/config/runtime activation, real provider or owner-local app run.

- [ ] P8-T18 — real model test runs in existing single-owner mode without
  personal billing/workspace setup; enabled personal mode retains trusted actor
  payer, workspace and account/budget admission; remove disabled-billing warning;
  preserve answer/JSON/statuses and normal model usage; no activation/migrations;
  owns: model-test endpoint/actions/probe/client diagnostic label/template/JS,
  focused tests/docs; owner: remote Notion agent (`fix/admin-model-owner-mode`);
  status: BLOCKED only for deferred owner-local acceptance, not implementation.
  Owner explicitly clarified that disabled personal billing is normal single-owner
  operation and must not block real tests. Evidence: 178 isolated tests passed;
  80 offline Chromium viewport/theme/state cases plus owner blank/custom submit
  without workspace, personal workspace validation, no-JS, JSON copy/fallback,
  busy guard, keyboard and focus checks passed. Source uses the actual client
  flag-off admission branch over MockTransport; storage/metrics are explicit
  doubles, not a real DB/ledger acceptance claim. Scope formatting, compilation,
  JS syntax and diff checks passed. Prepared local acceptance covers one owner
  request, model counter/cost readback and zero personal dispatches; not executed
  remotely. Existing personal payer/admission/settlement tests remain. Owner's
  in-session dev delivery/local-validation deferral remains in force. No setting
  activation, schema change, runtime, live provider call or deployment.

- [x] P8-T19 — audit collect force-refresh semantics and task-run feedback; accept:
  correlate both Safari launch actions with persisted Jobs and source outcomes,
  document raw/analytics overwrite behavior and confirmed failure cause, preserve
  existing data, and record bounded follow-ups. Owner: codex-collect-repro;
  status: DONE. Identifier-only reconciliation: this audit formerly duplicated
  P8-T17 (the existing admin diagnostics task); it is now P8-T19. Ownership,
  acceptance and evidence are unchanged. Accepted by coordinating Codex on 2026-10-17 against the source
  SHA below and the report diff. Evidence: [collection launch audit](../audits/2026-10-17-collection-launch.md)
  reviewed at source SHA `b70911d6c1e0c6a71e5668a502df42f1e73fefa9`. Jobs 1737 and
  1738 were correlated to the two Safari actions; each ended after about five
  seconds with one VK source error and zero collected records. The reported
  missing modal was not reproduced. The screen-name lookup failed with
  `httpx.ConnectTimeout`; the precise network cause is unknown. Public raw and
  analytics data were unchanged. No code or tests were changed or run. The
  confirmed task-summary `ok` despite all-source errors is recorded as a bounded
  follow-up; no speculative launch fix was applied.

- [ ] P8-T20 — verify OpenRouter token/cost evidence (including explicit zero),
  model-test persistence and response-only truncation; replace JSON copy text
  control with accessible corner SVG feedback and semantic status text colors.
  Scope: model-test UI, focused cost extraction if needed, regression tests/docs.
  Owner: remote Notion agent (`fix/admin-model-cost-feedback`); status: BLOCKED (implementation ready;
  owner-local PostgreSQL/ASGI acceptance remains deferred under the explicit
  in-session dev delivery/merge exception).
  Acceptance: supplied 470/8/478, zero-cost response has faithful cost evidence;
  accessible copy success on icon only, manual fallback retained; dark/light and
  narrow layouts; no real provider/DB calls remotely, local persistence deferred.
  Identifier-only synchronization: P8-T19 was reserved by the merged collection
  audit before publication; this lane now uses P8-T20. Foreign owners, evidence
  and statuses are preserved. Synced base: `18a2cb6`.
  Evidence: 198 isolated tests passed (0 failed/skipped) on synchronized dev,
  including actual source over HTTP/storage doubles, canonical structured USD
  evidence and journal pricing. 144 offline Chromium viewport/theme/state cases
  (320/390/1440 px; explicit/system light/dark) plus clipboard success/reset,
  denial/manual selection, SVG position/44px target, keyboard, no-JS, submit and
  busy/bfcache safeguards passed. Semantic text contrast >=4.5 checked. Scoped
  Black/isort, Python compilation, JS syntax and diff checks passed. Prepared
  real DB readback case was updated, not remotely executed. API price is separate
  from the existing last-request tariff estimate; no token-history guarantee is
  added for unscoped owner mode. No schema/config/runtime/live provider/deploy.
  Remote tests are self-executed per the owner's explicit exception, now recorded
  in the remote skill; shared-doc sync follows the existing all-agent procedure.

- [x] P8-T21 — add/edit related FK/m2m records without losing the parent form.
  Accept: permission-aware controls on existing editable relationships; normal
  guarded CRUD in a separate window; successful save updates the correct select,
  preserves other m2m selections and unsaved parent input; cancellation, blocked
  popup, validation errors and hostile messages do not mutate the selection.
  No new catalog privileges, raw FK fields, ORM writes, schema or live actions.
  Depends: none; owns: app/admin/related.py, scoped setup/template integration,
  app/admin/base.py (form converter), app/static/{js,css}/admin-related.* and
  focused tests/docs/ADMIN.md; owner: remote
  Notion agent (`feat/admin-related-records`); status: DONE (owner accepted the bounded implementation and explicitly
  authorized Ready/merge after reporting that the updated local UI works).
  Synchronized base: `1e0e3a0c772c4a7bcc04862434c172d32288868e`. Active PRs #216
  (P10-T11) and #217 (P10-T12) inspected; their reservations, ownership and
  evidence remain on their own branches, with no code ownership conflict.
  Evidence at `6f1f695dec730396bda79043d19414fea13a2094`: 20 isolated tests passed
  (exit 0, no failures/skips), 32 offline Chromium assertions at 390/1440px
  (exit 0, all requests intercepted); actual target route/fragment behavior
  over synthetic ORM and explicit storage doubles, not real DB acceptance.
  [Contract, commands, versions and local handoff](../ADMIN.md#related-fkm2m-record-editors).
  Follow-up on owner feedback: inline plus/pencil SVG controls (not text buttons
  below the field); canonical Tenant Float `NoConverterFound` reproduced on
  SQLAlchemy 2.1/SQLAdmin 0.32 and fixed in BaseAdmin without converting precise
  Numeric/Decimal money. 81 isolated tests + 228 offline browser assertions
  passed (exit 0; 0 failed/skipped), including real Select2 and canonical Tenant
  create/edit/related GET scaffolding. Original evidence remains attributed above;
  local reported 500 is failure evidence only. Exact follow-up published SHA in
  PR #219 handoff. Owner subsequently reported that the updated local UI works
  and requested closeout/Ready/merge. This is owner-reported functional smoke,
  not evidence that the full PostgreSQL/revocation/rollback checklist ran.
  Accepted source: `aec048db192bfc9842883502065646d5650f43d9`; reviewed against
  fresh dev `1e0e3a0c772c4a7bcc04862434c172d32288868e`, no conflicting changes
  or review threads; GitGuardian check passed. Prior failures and sandbox evidence
  remain attributed above. Phase/gate approval, full-suite, deployment and schema
  acceptance are not implied by this task closure.

- [ ] P8-T22 — audit and simplify the social analysis web journey; accept:
  one social analysis task choice collects then analyzes, standalone CLI/API/chat
  operations remain compatible, partial analysis retains staged input; scenario,
  source and task pages explain their distinct setup/actions/statistics using
  shared shell styles and permission-gated diagnostics; focused backend/render
  checks include the final task editor snapshot; visual testing excluded by
  the owner's later instruction; depends: none;
  owns: web task backend and scenario/source/task templates, shared web
  presentation helpers, final editor follow-up after the explicit P8-T23 handoff,
  focused tests and subsystem/audit docs; owner: codex-social-analysis;
  status: BLOCKED. Base: local dev `c98dc9c`, origin/dev `1e0e3a0`
  verified 2026-10-18; source-picker changes preserved. Task editor changes
  are serialized after its active source-picker work finishes.
  Implementation and isolated verification recorded at `a11f1f9` plus reviewed
  working diff: 210 isolated pytest cases passed through primary OpenCode;
  Node cron checks and Safari 27.0 actual synthetic POST checks passed. See
  [audit and layout contract](../audits/2026-10-18-social-analysis-web.md).
  Persisted PostgreSQL acceptance remains blocked: the local
  test schema lacks `analyzed_content_receipts`; no migration/reset authorized.
  The live site returned 502. No paid collection/model run or full responsive
  visual acceptance is claimed. The final compact selector/button styling
  follow-up received syntax and diff checks only; it was not visually tested.
  Source-detail follow-up claimed 2026-10-19 at `401a636`, synchronized with
  the intervening owner commits through `346cd65`, by
  codex-social-analysis: retain source aggregates/access and related task links,
  move all mutable collection settings into an inline form, remove source-page
  run/history UI and header actions, and keep one source-prefilled task CTA.
  Existing unrelated working changes and exposed compatibility routes are
  preserved. Python/Jinja syntax and diff checks passed. Primary OpenCode
  collected 122 isolated cases: 118 passed; four editor cases exposed a missing
  constant in the AST test fixture. After that fixture-only correction, the
  four cases passed in a focused rerun (0 failed/skipped); application code
  remained unchanged. No visual or database-backed follow-up is claimed.
  Task-detail follow-up: moved latest task-associated run counters, per-source
  progress, raw-data coverage and admin-only diagnostics onto task detail; task
  navbar now exposes edit and run now, with retry in the run panel. Retry resets
  attempts only for that task's source/date scope and rolls back on busy
  admission. The canonical editor is reused in a modal; save returns to task
  detail, and task names link to detail. Current raw backlog uses authorized,
  windowed batch counts; model-call diagnostics match exact task and Job.
  Synchronized with the unrelated admin integration at `ddc9b99`, preserving
  its accepted P8-T21. Primary OpenCode collected 131 no-DB cases: 130 passed
  initially and one text assertion was corrected; targeted rerun passed 21/21
  cases after the bound-manager correction, leaving the other 110 unchanged
  passing cases valid. Python/Jinja/Node syntax and diff checks passed. Actual
  read-only ORM access for task 1582 passed; the local URI still returns 502.
  No browser, live execution or persisted worker acceptance is claimed.
  Closeout 2026-10-19: implementation reviewed for the authorized local commit;
  source/task implementation committed as
  `ca4b9b04cb42396765b0d5816abce0f67d8e21df`.
  Persisted acceptance remains BLOCKED. A fresh catalog query using the existing
  session factory in a read-only transaction confirmed that
  `test_schema.analyzed_content_receipts` is absent. No schema change was made;
  R8 requires explicit authorization for migration. No other active task's
  acceptance is implied. Visual checks are excluded by the owner's instruction.

- [x] P8-T23 — compact inline source picker on the task edit page; accept:
  selected sources render as inline chips with a remove control, adding uses
  a searchable list with shared styles, POST semantics of `source_ids` stay
  unchanged, and no-JS checkbox fallback is retained. Depends: none; owns:
  `app/web/templates/web/task_edit.html`, focused render test; owner: $local;
  status: DONE — accepted: codex, 2026-10-19, implementation commit
  `a11f1f9e880925b01bf30478dbde62fa6ad0b48c`; isolated no-DB pytest 53/53 passed
  (`tests/unit_web/test_task_source_picker.py`,
  `tests/unit_web/test_template_autoescape.py`); rendered script Node syntax
  check passed. Integration `tests/test_web_task_edit.py` blocked before setup
  by pre-existing test-schema drift (`analyzed_content_receipts` absent);
  schema unchanged. Identifier-only reconciliation after local merge
  `ddc9b99`: this source-picker row formerly duplicated P8-T21, now reserved
  by the accepted admin related-record editor. Ownership, status and evidence
  of both tasks are preserved.
  Coordinator closeout review found the picker acceptance met; the separately
  blocked PostgreSQL suite is retained as a documented integration limit and
  does not claim persisted worker acceptance. Removed its open registry row.

## Phase approval

- [x] P8-T24 — keep incomplete-analysis recovery and its progress on analysis
  detail; accept: explicit retry launches only after committed admission even
  without the runtime worker, queue/running/unconfirmed states reflect stored
  evidence, shared progress UI renders inline, and content refreshes after
  completion. Task links replace source/queue detours, the detail layout keeps
  useful stored content and hides empty sections. Preserve tenant/permission,
  exact-window and transaction guards; no live model calls in verification.
  Depends: none; owns: analytics web retry/detail routes and templates, shared
  progress presentation, focused regression tests and analytics docs;
  owner: codex-analytics-inline-retry; status: DONE — accepted: codex,
  2026-10-19, implementation commit
  `78f7003dce35e15bbf4ee1be5a59873b7447bf73`.
  Claimed 2026-10-19 at local dev `ddc9b99`; preserve the current uncommitted
  source/task follow-up and accepted unrelated admin integration.
  Evidence 2026-10-19: reviewed the uncommitted diff on base `ddc9b99`.
  Explicit retries dispatch only after admission commits; compatible old pending
  retries require a guarded POST to resume. Inline polling has no write effects;
  queue and stale-heartbeat states do not claim provider activity. Fresh saved
  result IDs drive completion navigation, while related tasks replace source
  detours. Complete summaries with legacy metrics do not trigger recovery reads.
  Primary OpenCode `QwenPlan/deepseek-v4-flash-0731` collected and passed 232
  no-DB tests (0 failed, 0 skipped; 35 subtests passed) using
  `--noconftest -o addopts= -q --tb=short --no-header` across:
  `tests/unit_web/test_analysis_retry.py`,
  `tests/unit_web/test_analytics_inline_progress.py`,
  `tests/unit_web/test_analysis_result_ids.py`,
  `tests/unit_web/test_run_progress_component.py`,
  `tests/unit_web/test_analysis_content_layout.py`,
  `tests/unit_web/test_task_detail_progress.py`,
  `tests/unit_web/test_template_autoescape.py`,
  `tests/test_analysis_progress_endpoints.py`,
  `tests/test_inline_analysis_errors.py`, and
  `tests/test_analyzer_reported_errors.py`.
  Updated stale modality-model and durable-stage test doubles without weakening
  failure assertions. `git diff --check` passed. No migration, browser checks,
  runtime start, live Job dispatch, provider call or database write was performed.
  Coordinator reviewed implementation commits `ca4b9b0` and `78f7003` at
  closeout; the committed app/test contents match the verified working tree.
  Accepted this bounded no-DB contract and removed its open registry row;
  persisted worker/billing acceptance and phase approval are not implied.

- [ ] P8-T25 — settings information layout and compact member access; accept:
  replace the two segment rows with one settings navigation, describe each
  section, align forms/buttons, put the model test beside its selector, and
  compact invitation/member controls without changing routes or permissions.
  Depends: none; owns: settings template/CSS, invite partial presentation and
  focused offline settings tests; owner: remote Notion agent;
  status: READY for coordinator review in PR #222; implementation
  `500900ba86293f063d0bf0bce6f551be788dc023`. Synchronized dev
  `959a461410aa220cd1059c4e3ee59b55d5f79eaa` and active PRs
  #216/#217/#220/#221 rechecked 2026-10-19; foreign owners/evidence preserved.
  Sandbox evidence at that exact implementation SHA: 109 isolated pytest
  checks passed (0 failed/skipped) across `test_settings_layout.py`,
  `test_settings_scopes.py` and `test_template_autoescape.py`, with
  `--noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header`.
  `tests/settings_layout_fixtures.py` plus `tests/web_settings_layout.cjs`
  passed 104 synthetic browser cases: 13 settings states, widths
  375/390/768/1440, light/dark. All requests intercepted or aborted; checked
  navigation, model-probe/save separation, native validation, role/AI/access
  forms, confirmation cancel/accept, lazy invites, issue/revoke/result and
  control overflow. Representative route, mobile/desktop, dark/light,
  expanded/empty/error and dialog screenshots visually inspected; long model
  labels remain two lines. Six new text/accent contrast pairs exceed 4.5:1.
  Environment: isolated venv, Python 3.13.14, pytest 9.1.1,
  pytest-asyncio 1.4.0, Jinja2 3.1.6, Playwright 1.63.0, Chromium 153.0.8010.12.
  Black/isort, JS syntax and diff checks passed. Two stale settings-test user
  fixtures gained the current shell's `role` field; no assertions weakened.
  No handler, permission policy, schema or production dependency changed;
  no application/runtime start, real DB/provider, migration or deployment.
  Synthetic checks are not full-suite/real permission/persistence acceptance.
  READY is not DONE or merge approval; coordinator accepts the exact head.

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
