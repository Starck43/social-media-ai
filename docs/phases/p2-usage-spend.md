# P2 — Complete usage and spend (gate: usage and spend are complete and attributable)

Goal: no request starts without committed admission under the current cap;
parallel attempts cannot bypass it; undefined cost is never free or zero;
`ai_analytics`, `jobs.llm_cost`, `digest_runs.llm_cost` stay projections; no
double counting.
Delivered: paid-attempt accounting separate from daily analytics (PR #163);
journal evidence for analyzer and every non-analyzer paid attempt (PR #114).
Merged, UNWIRED: atomic receipt/reservation storage and owned-transaction
admission API (PR #180, merge `df06b1e`); isolated PG acceptance recorded in
IMPLEMENTATION_STATUS. Owner pricing decision (2026-10-12): FREE only on an
explicit zero; structured provider USD price arbitrates cost; no resolvable
price is UNDEFINED — never free, never zero, always tracked.
Owner billing decision: user owns a personal billing account; a workspace is
access/execution context; every task has a fixed payer for manual and
background runs; changing payer requires new payer consent.

## Tasks

- [x] P2-T11 — align chat, digest and analyzer cost projections with resolved structured provider USD totals, including explicit zero and missing token counts; accept: mocked adapter and analyzer regressions, provider-total precedence without live calls, existing tariff estimate retained only when no resolved report; depends: P2-T10; owns: app/services/ai/llm_client.py, app/services/ai/analyzer.py, tests/test_llm_usage_cost.py, tests/test_analyzer_provider_cost.py, tests/test_analyzer_reported_errors.py (isolated parser binding only), tests/test_scoped_attempt_journal.py, tests/test_analyzer_attempt_journal.py (stale fixtures), docs/audits/2026-10-20-token-cache-cost.md; owner: codex-token-cost; status: DONE — OpenCode focused no-DB collect 104 / execution 104 passed, 0 failed/skipped (43 subtests), base `5146696` plus seven-file digest `6105f007cafc2d460859e921a25fa58429dd0b5eb59de5f61e56688a7b91cbe2`; Coordinator accepted integrated sources: candidate `b4f6d39` merged with dev `adf1d6f`; repeat OpenCode collect 104 / execution 104 passed, 0 failed/skipped, 43 subtests; seven-file digest `0ab5a38d35640fc9682bf89d0c594f2b2a935ebfc57d3031e247aec6c8abe9ab`. Base: `5146696`, dev `de9fe31`; parallel dev edits excluded. This task does not certify cache tariff accuracy or unknown-cost cap handling.

- [x] P2-T12 — preserve provider-specific cache usage and version complete dispatch-time cache tariffs; accept: ordinary/read/write/TTL semantics, missing versus zero, reviewed migration, immutable replay, and unknown cost when applicable categories/rates are unavailable; depends: P2-T11; owns: normalized usage, model/receipt/attempt/event tariff evidence and migrations, focused tests; owner: codex-cache-pricing; status: DONE at exact reviewed and verified dev `cac78c5da1400ed15a064e4fe3a9e0b4dfa8f430`. Runtime/test implementation is in `476ba78`; the integrated test bootstrap yield fix is `307e021`. OpenCode River: `tests/billing/test_unknown_cost_propagation.py` collect 26 / run 26 passed, 0 failed/skipped; `tests/acceptance/test_cache_tariff_receipt_pg.py` collect 8 / run 8 passed, 0 failed/skipped, root conftest enabled on `test_schema` revision `0110`. The PG checks verify persisted v2 rates/token unit, immutable identical replay, refusal of changed cache-read/cache-write-5m/cache-write-1h/token-unit evidence, and pricing from the frozen snapshot. The integration run added 8 retained synthetic receipts (24 `ctp-model` rows total); no cleanup was performed. A verified private test-schema backup was retained before canonical reference seeding; no reset, DDL, or migration was run during preparation/testing. `public` was already at Alembic `0110`, including P2 migration `0107`, so no further migration was pending or applied. No UI surface changed; browser testing was not in scope. Backup: `/private/tmp/p2-t12-test-schema-backups/test_schema-before-p2-t12-20261025.dump`, SHA256 `f67e0a1749688449a341651b575accb5dce90b9fd116374f9a036ac8e0fe8783`. Earlier evidence at `f220725`, `7884c3a`, and `e446c044a863d641b27714a4bffe8563ecbb91dd` remains attributed to those revisions. See [audit](../audits/2026-10-20-token-cache-cost.md).
  Historical coordinator review on 2026-10-21 at `f55abad30adbba9b411b31207dfa155423d490c6` returned an earlier READY claim to IN_PROGRESS because cost-certainty propagation and PostgreSQL replay evidence were incomplete at that time. The accepted record above supersedes that status. Browser scope remained limited to an exposed unknown/estimated-cost presentation; no UI surface changed in the accepted work. [Historical review](../audits/2026-10-21-ready-pr-audit.md).
  2026-10-24 claim by `opencode` for the outstanding unknown-cost criterion only
  (cache tariffs, migration 0107, receipts and journal evidence are already in
  dev). Scope: transport fallbacks that publish a numeric `0.0` where the cost
  is unresolved — `LLMClient._usage_block_for_response` invalid-claim branch and
  `_analyze_usage_projection` exception branch — plus consumers that must treat
  that as unknown (`agent_messages.record_usage`, `jobs.llm_cost`,
  `digest_runs.llm_cost`) and the digest builder reading a flat-tariff estimate
  as a known provider total. The analyzer already keeps `NULL` for resolved
  unknown spend (`_price_usage`). No migration, schema or tariff change.
  Delivered on local dev `f220725` (2026-10-24): transport fallbacks keep the
  cost unresolved instead of publishing `0.0`, consumers carry the unknown
  through, and `tests/billing/test_unknown_cost_propagation.py` pins the
  propagation. This was a partial delivery at `f220725`.
  Coordinator completion claim on 2026-10-25: integrated source at `476ba78`
  plus the bootstrap-fixture repair `307e021` passed both focused scopes at
  `cac78c5da1400ed15a064e4fe3a9e0b4dfa8f430`. The no-payload fallback preserves
  unknown cost, chat persists cost only through the shared certainty helper,
  and replay evidence is isolated from timestamps. Earlier evidence at
  `f220725` and `7884c3a` remains attributed to those SHAs.

- [x] P2-T13 — bind journal tariffs to exact provider/model identity; accept: same model_id across paid/free providers cannot resolve another provider's tariff, fail closed on ambiguity, regression evidence; depends: P2-T10; owns: journal tariff resolver and focused tests; owner: codex-token-cost; status: DONE at dev `b6efff9cac5f53827b8adbf6ae7a97c5e7bd9965`. Dispatch-time model row ID and normalized provider/model identity now select the tariff; missing, mismatched or ambiguous legacy identity stays unpriced. Verified with isolated no-DB tests `test_llm_attempt_journal.py` 30/30 and `test_scoped_attempt_journal.py` 16/16 (46 passed, 0 failed/skipped; 8 subtests) via Codex test subagent fallback after OpenCode initialization and Kilo availability failed. See [audit](../audits/2026-10-20-token-cache-cost.md).

- [x] P2-T14 — use complete spend evidence in caps and payer/workspace reports; accept: finite personal caps fail closed when the obligation has no safe bound, unknown obligations remain held, provider-reported/reconstructed/reserved/unknown values are separated, aggregation uses one canonical dispatch record and never adds projections, scope is payer+workspace, focused PostgreSQL checks sequential through R10; depends: P2-T11, P2-T12, P2-T13; owns: personal reservation/cap consumers, scoped spend report, focused tests; owner: codex-token-cost; status: DONE at `626176328c04ac90d7c18f95dd0ed38d571e7f88`, merged into local dev. Undefined/unbounded obligations now fail closed for finite caps and unresolved historical holds stay unknown; payer/day summaries separate provider-reported, receipt-reconstructed, reserved, and unknown amounts per canonical dispatch, scoped by workspace without counting projections. OpenCode initialization failed and Kilo was unavailable, so the authorized Codex test subagent executed the bounded checks: PostgreSQL `tests/test_personal_billing_ledger_db.py` 8 passed; four no-DB files 56 passed, 0 failed/skipped. The PostgreSQL run used `test_schema` at 0108; `public` remained at 0107. See [audit](../audits/2026-10-20-token-cache-cost.md).

- [x] P2-T10 — audit cache-aware token pricing, reported costs, daily caps and payer/workspace attribution; accept: source-backed findings and bounded correction plan, no runtime changes; depends: none; owns: docs/audits/2026-10-20-token-cache-cost.md; owner: codex-token-cost-audit; status: DONE — coordinator static review at `de9fe31`, 2026-10-20; evidence: [audit](../audits/2026-10-20-token-cache-cost.md); runtime corrections and pytest remain unperformed.

- [x] P2-T01 — model personal billing account identity and fixed task payer
  binding; changing payer requires new payer consent; accept: personal billing
  account identity, fixed task payer for manual and background runs, consent
  on change, admission invariants above preserved;
  depends: none; owns: billing/payer modeling and application;
  owner: billing-agent; status: DONE — accepted: coordinator in this session
  under explicit owner instruction, 2026-10-17, dev `c18ee2f`. Merged via
  PR #205 `b0bd202` (tree identical to tested HEAD `0b1fd71d`). Code:
  `PersonalBillingAccount`/`TaskBillingPayer` (`app/services/billing/payer.py`),
  `consent_legacy_attribution`, migrations 0097/0099/0100/0103. Evidence:
  P2-T07 no-DB 77/77, post-0103 PG 15/15 (5 personal-ledger), P8-T14 remote
  110/110 + local 41/41. Scope split: VIEW-only DB login not tested;
  activation/cutover not included.

- [x] P2-T02 — admin connection-test payer: paid probes charged to the
  launching user's personal account; accept: decision enforced for admin paid
  probes, requires platform admin rights; depends: P2-T01;
  owner: billing-agent; status: DONE — accepted: coordinator in this session
  under explicit owner instruction, 2026-10-17, dev `c18ee2f`. This is the
  exact slice accepted as P8-T14 (admin probe → launching user's personal
  account). Code: `app/services/ai/model_probe.py:131` `probe_real_capped`,
  admin route `app/admin/views.py:1329`. Evidence: P8-T14 remote 110/110,
  local acceptance 41/41 at tested HEAD `0b1fd71d`; merged via PR #205
  `b0bd202`. Activation not included.

- [x] P2-T03 — explicit compatible transition and mapping for legacy finance
  records and tasks; accept: ambiguous records are never automatically
  reassigned; depends: P2-T01; owner: billing-agent; status: DONE — accepted:
  coordinator in this session under explicit owner instruction, 2026-10-17,
  dev `c18ee2f`. Code: `consent_legacy_attribution`
  (`app/models/managers/personal_billing_manager.py:390`), migration 0100;
  explicit per-identity owner consent, no automatic reassignment. Activation
  not included.

- [x] P2-T04 — atomic idempotent settlement/reconciliation;
  accept: uncertain obligations preserved after ambiguous outcomes;
  depends: P2-T01, P2-T03; owner: billing-agent; status: DONE — accepted:
  coordinator in this session under explicit owner instruction, 2026-10-17,
  dev `689c6fb` (current workspace HEAD). Owner-authorized test_schema
  reconciliation applied the migration guard DDL (functions/triggers) from
  revisions 0093/0094/0095/0097/0099/0100/0102 in place via the documented
  ACCESS_MIGRATION precedent (one transaction, no version table, current
  tables retained). PG/ASGI acceptance
  `tests/test_personal_billing_ledger_db.py` 5/5 PASS at the reconciled
  schema, covering settle/release/freeze on overshoot, unknown-cost
  failure + idempotent replay, and trusted operator task initiator
  (rev 0103 validator). Evidence: `.agents/tmp/test-report.md`;
  same run earlier passed no-DB 77/77 + post-0103 PG 15/15 on an
  earlier consistent schema. Uncertain obligations are preserved via
  incident rows and day freeze; production settlement/verification is
  deferred to P2-T09. Activation not included.

- [x] P2-T05 — provision UTC daily baselines; accept: reviewed upper bounds
  for paid attempts; depends: P2-T04; owner: billing-agent; status: DONE —
  accepted: coordinator in this session under explicit owner instruction,
  2026-10-17, dev `c18ee2f`. Code: cutover (`establish_clean_cutover`),
  `review_bound_policy` (`personal_billing_manager.py:262,317`), per-day
  `baseline_version` (`personal_llm_billing_manager.py:386`). Note: parent
  P2-T04 (reconciliation) is not accepted; settlement-layer verification is
  deferred to P2-T09 (production check). Activation not included.

- [x] P2-T06 — wire admission through all provider clients and callers;
  accept: no fallback around admission denial; depends: P2-T02–P2-T05;
  owner: billing-agent; status: DONE — accepted: coordinator in this session
  under explicit owner instruction, 2026-10-17, dev `7e5b4f1`. Evidence:
  focused no-DB run `tests/test_personal_billing_foundation.py` +
  `tests/test_personal_billing_transport.py` 17/17 PASS, including
  `test_denied_admission_sends_no_provider_request` and
  `test_fallback_does_not_try_next_model_after_billing_denial`; combined with
  previously accepted P8-T14 evidence (remote 110/110, local 41/41 at
  `0b1fd71d`). Code: `_personal_admission`/`_observe_personal_outcome` in all
  clients (`llm_client.py`), `raise_personal_billing_block` guards the
  fallback chain (`app/services/billing/errors.py:26`). Activation not
  included.

- [x] P2-T07 — focused PG/ASGI acceptance and cutover;
  accept: per acceptance recorded via R10; depends: P2-T01–P2-T06
  prerequisites; owner: codex; status: DROPPED — superseded by owner deferral
  2026-10-17: acceptance evidence is already recorded (integrated no-DB
  77/77; post-0103 PG 15/15 on a previously consistent schema), and the
  remaining production activation/verification is moved to P2-T09 (owner
  deferred). Dropped in this session under explicit owner instruction at dev
  `7e5b4f1`; not replaced by a new local acceptance task.

- [ ] P2-T08 — corporate/shared billing accounts; accept: keep clear deferred
  status, no implementation now; depends: owner deferral;
  owner: product-owner; status: TODO — deferred by owner

- [ ] P2-T09 — production billing verification; accept: independently verified
  live billing behavior after production activation — admission wired through
  real provider calls, settlement/reconciliation with actual outcomes, legacy
  consent/payer flows; reserved as a later production-only check
  (not bound to the current cutover); depends: production activation/cutover,
  P2-T04 settlement acceptance; owner: unassigned; status: TODO — deferred by
  owner; must be performed in production after activation, not now.

## PR197 disposition (coordinator decision, 2026-10-17, dev `c18ee2f`)

Decision: PR #197 (`feat/reviewed-budget-baseline`, tenant-scoped frozen
`provision_reviewed_day` + `llm_budget_baselines` + its own conflicting 0097)
is SUPERSEDED for the active billing lane by the merged personal billing
foundation (0097 personal foundation, 0099 ledger, 0100 legacy attribution,
PR #205). A separate tenant-based `provision_reviewed_day` is NOT needed now;
it is deferred to P2-T08 (corporate/shared accounts, owner-deferred). PR #197
stays Draft/CONFLICTING; no renumber, migration edit, merge or closure is
authorized by this note. Owner: coordinator (acceptance), billing-agent
(P2-T05/P2-T08 follow-through).

Requirement mapping vs the current personal API:

- Immutable reviewed provenance — NOT EQUIVALENT. Personal API records a
  one-time account-level reviewed boundary (`PersonalBillingCutover`,
  `establish_clean_cutover` in `app/models/managers/personal_billing_manager.py:262`)
  plus immutable reviewed request envelopes (`review_bound_policy`, same file
  :317). There is no per-UTC-day frozen snapshot with reviewer/evidence-SHA/
  fence attestation as in PR197 (`app/models/managers/llm_budget_manager.py:30`
  and `app/services/ai/budget_baselines.py:42` at `ad912d4`). Per-day reviewed
  provenance becomes separate work only with P2-T08.
- Exact replay without touching balances/counters/freeze — NOT EQUIVALENT
  (deliberately absent). `reserve_dispatch` mints a server-side `dispatch_id`
  (`app/models/managers/personal_llm_billing_manager.py:133`), so caller replay
  cannot occur; duplicate receipt identity fails closed with
  `PersonalDispatchConflict` (:432). The tenant-lane replay-comparison in dev's
  `reserve_receipt` is unreachable because nothing in dev creates tenant
  `LLMBudgetDay` baselines (no creator merged; admission fails closed at
  `app/models/managers/llm_budget_manager.py` "baseline is unavailable or
  frozen"). Requires separate work only if the tenant lane is revived.
- Acknowledged commit — IMPLEMENTED (convention-equivalent). Both lanes use the
  same owned-session commit convention (`_get_owned_session`,
  `app/models/managers/base_manager.py`); PR197's created-vs-replay boolean has
  no personal counterpart because replay does not exist, and ambiguity still
  fails closed via exceptions.
- Legacy/orphan refusal — IMPLEMENTED (different mechanism). Pre-boundary days
  are refused (`personal_llm_billing_manager.py:331,367`), and legacy finance
  records are never silently adopted — only explicit per-identity owner consent
  (`consent_legacy_attribution`, `personal_billing_manager.py:390`, migration
  0100, P2-T03 lane). PR197's "day without baseline → conflict" case cannot
  arise: personal days are admission-created only after the cutover.

Follow-ups (bounded, no action taken in this note): dev-local PR197 acceptance
assets from `08dc61a` (`scripts/pr197_normal_migration_launcher.py`,
`tests/acceptance/test_pr197_reviewed_baseline_pg.py`,
`tests/test_pr197_offline_gates.py`) target PR197's conflicting 0097 and are
inert on the merged chain; their retention/removal and PR #197 closure need
separate owner authorization. Next step: none for the personal lane; revisit
under P2-T05/P2-T08 if tenant baselines are re-scoped.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
