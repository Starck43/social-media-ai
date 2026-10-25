# P10 — Source-audit hardening (gate: the audited runtime defects are closed)

Phase scope and accepted-task history. [Active index](../../ROADMAP.md#p10);
detailed cards are siblings in this directory. [Product direction](../PRODUCT_PLAN.md#foundation-directions).
Historical instructions/permissions below are evidence, not current policy.

Goal: implement the runtime fixes from the source-pinned audit. Baseline
`7ba5fe4`; report, evidence and exclusive per-task file ownership live in
[the audit branch](https://github.com/Starck43/social-media-ai/pull/196)
(`docs/audit/2026-10-15-dev.md` on that branch). Risk-based source coverage,
not a full security/production certification. One bounded task per
implementation; serialize shared-file lanes and all DB acceptance under R10.

## Closed tasks

- [x] P10-T17 — compact agent entry/workflow and lazy task documentation; owner: codex; status: DONE. Accepted by Codex, 2026-10-25T10:58:38Z, exact reviewed source `b8dac6011463075973be8b8645b32d70167c84e4`. ROADMAP is a short index with linked task cards and actual docs-read/check logs; duplicated overview/rules consolidated. Checks: 0 missing local links/anchors, 27 original active tasks and 69 prior closed records preserved, 10 YAML headers/3 TOML configs valid, diff whitespace PASS; no pytest (docs-only). [Evidence/docs read](../audit/p10-t17.md); [status dialogue](/Users/admin/.codex/sessions/2026/10/25/rollout-2026-10-25T12-39-07-01a1726d-96c3-7083-8828-2a2a1558676b.jsonl). Integration: pending on task branch; housekeeping is separate.

- [x] P10-T02 (AUD-02, P1) — remove password hashes from HTTP user responses;
  accept: HTTP/OpenAPI profiles exclude password hashes, existing rights
  preserved; depends: serialize `user.py`/schema writes with P10-T03;
  owns: tests/test_http_user_response_privacy.py, docs/validation/http-user-response-privacy.md
  and scoped Users response contract in docs/API.md;
  owner: remote Notion agent (fix/http-user-response-redaction); status: DONE.
  Claim before implementation from dev `aeb8c54d74a55710b5080fd0a980fbc0ecf36d8c`.
  Current UserInDB already excludes hashes (existing `eb6cad6`); this lane adds
  bounded HTTP/OpenAPI regression evidence and corrects stale response docs,
  not another runtime/schema or privilege redesign. P10-T03 remains unassigned
  and unchanged; no shared user.py/schema writes are planned. Open PRs
  #216/#217/#220/#224/#225/#226 checked; docs/API.md foreign hunks do not overlap
  the Users response section. Preserve other claims/evidence and Draft PRs.
  Claim: `6093a08597cfa46a430c628d74fb291060c8deb9`;
  test/API-doc SHA: `35df749c1ba3b29d71b99089d072f96f00d8e814`.
  Draft [PR #227](https://github.com/Starck43/social-media-ai/pull/227).
  Remote self-run at the published SHA: 47 isolated tests passed, exit 0,
  0 skips/xfails; five HTTP profile-response routes, actual schemas/OpenAPI
  and route guards with explicit identity/storage doubles. Intentional hash
  regression in a disposable copy: 18 failed / 29 passed, exit 1 as expected.
  Black/isort, pip and scoped diff checks passed. No production code changes.
  Commands, dependency versions, harness correction and limits:
  [validation](https://github.com/Starck43/social-media-ai/blob/987796aa01ba9dd924207512b2a314bb63fbda97/docs/validation/http-user-response-privacy.md).
  Owner R10 integration acceptance against exact PR source
  `987796aa01ba9dd924207512b2a314bb63fbda97`; fresh dev at test time:
  `e446c044a863d641b27714a4bffe8563ecbb91dd`. OpenCode, Python 3.12.6,
  pytest 9.1.1, FastAPI 0.143.2, httpx 0.28.1, SQLAlchemy 2.1.4,
  greenlet 3.5.6: 2 passed, 0 failed/skipped, 6 warnings, exit 0.
  Actual app OpenAPI plus real ASGI/JWT/middleware/ORM list, me, self-update
  and cross-user profile routes: no hash disclosure; 401/403 denials, permitted
  profile updates and no denied persistence; role/hash unchanged. No dependency,
  identity, storage, manager or AST-policy doubles. Scratch module SHA256
  `825be27eb62e867bd92f346df200702c07e84301274c50f4111349d4d5e2c977`;
  command and attributed runner/preflight details:
  [owner integration evidence](https://github.com/Starck43/social-media-ai/pull/227#issuecomment-6300278265).
  The first unpaged list attempt exposed 262 pre-existing `@example.invalid`
  rows rejected by response EmailStr validation. The passing test paginated to
  its synthetic admin through the real manager and asserted that exact ID;
  foreign rows were untouched. Thus default pagination over those legacy rows
  remains a separate data-compatibility issue, not a hash/rights failure.
  Own synthetic rows were removed and absence verified. P10-T03, recovery,
  login protection, full-suite behavior and default-page legacy rows are not
  accepted by this task. No migrations, schema setup/reset, lifespan, providers
  or deployment. PR #227 is accepted and merged at the current closeout.
  Registry synchronized from PR head `987796aa01ba9dd924207512b2a314bb63fbda97`;
  implementation and test changes remain in that unmerged PR. Evidence above
  is attributed remote/owner evidence, not a new local execution.


- [x] P10-T05 (AUD-05, P2) — align legacy API DB probes with bounded redacted
  readiness; accept: bounded generic legacy probe failures; synthetic DB/DSN
  sentinels absent; root readiness unchanged; depends: none;
  owns: app/api/v1/entry.py, tests/api/test_legacy_api_db_probes.py and scoped
  docs/API.md health contract and docs/validation/legacy-api-db-probes.md;
  owner: remote Notion agent; status: DONE — accepted: codex, 2026-10-22,
  runtime SHA `ab532db952ab6137508a1fc2671b3a17bcacfdbf`.
  Synchronized base: `959a461410aa220cd1059c4e3ee59b55d5f79eaa`;
  active PRs #216/#217/#220 heads rechecked unchanged: no runtime/probe overlap;
  shared registry/phase edits remain task-scoped, foreign evidence preserved.
  Implementation/test SHA: `00f560e6236a43cd7f9ebeee9a0ab526a00d7618`.
  Remote self-run: 14 isolated tests passed, exit 0, no skips/xfails; final
  tests on the synchronized baseline: 8 failed / 6 passed, exit 1. Two-second
  cooperative ORM timeout, fixed legacy 500 detail, success/auth/cancellation
  preserved; root health source unchanged. Commands, environment, earlier failed
  test-harness run and limits: [validation](../audit/legacy-api-db-probes.md).
  READY is implementation review only, not DONE or DB/ASGI acceptance.
  Coordinator refresh 2026-10-21: implementation `00f560e` is already an
  ancestor of current dev; there is no open PR for this task to keep Draft.
  Owner-local acceptance applies to the current integrated implementation;
  no migration, reset, provider, runtime start or deployment is authorized.
  Current-source refresh at `f55abad30adbba9b411b31207dfa155423d490c6`:
  OpenCode `QwenPlan/deepseek-v4.1-flash-1017` collected and passed all
  14 isolated legacy-probe checks with `PYTHONPATH=tests` and the required
  quiet/no-conftest flags. This covers helper/route declarations and redacted
  fault/timeout behavior, not real middleware/auth or driver cleanup.
  READY remains review-only until the missing owner-local evidence is complete.
  The refresh waited for the foreign pytest sequence to finish, but subsequent
  OpenCode attempts failed certificate verification before schema preflight;
  Kilo CLI/orchestrator was unavailable. Real ASGI/DB/driver acceptance was not
  run and no current schema revision is claimed. [Refresh evidence](../audit/2026-10-21-ready-pr-audit.md).
  Acceptance completed by Codex on 2026-10-22 at
  `ab532db952ab6137508a1fc2671b3a17bcacfdbf`: source review confirms the real
  `/api/v1` registration, public legacy health and bearer/workspace guards on
  the hidden probes. Primary OpenCode `qwenplan/deepseek-v4.1-flash-1017`
  completed collection and execution through root conftest against the existing
  compatible `test_schema`, Alembic revision `0108`: **14 passed, 0 failed,
  0 skipped** across `tests/api/test_legacy_api_db_probes_acceptance.py` and
  `tests/api/test_api_health.py`. The new six-case acceptance suite covers real
  public health/ORM success, unauthenticated refusal before the probe, bearer
  authentication and identity with an existing active workspace membership,
  fixed redacted exception/timeout responses, and cancellation of an actual
  Permission-manager request delayed through asyncpg followed by a successful
  ORM probe. The root readiness/liveness regressions passed unchanged.
  Limitation: conftest uses NullPool, so this proves request/session cancellation
  and a subsequent driver checkout, not reuse of a pooled connection. Runtime
  files were verified unchanged from the recorded SHA; the acceptance file's
  SHA-256 is
  `a224e300abe73dd0348ec67f97db551b26486b7f8a8ff60dab33db247e3ae7c8`.
  Installed OpenCode rejected `--dir`; the identical attached task ran from the
  verified worktree instead. Shared-DB execution waited for the foreign test
  lane to finish. No schema changes, provider calls, runtime startup or deploy;
  `git diff --check` passed. Earlier pending-evidence notes above are historical
  and superseded by this local acceptance; no worker-health claim is made.

- [x] P10-T10 (AUD-10, P2) — distributable package runtime completeness;
  accept: nested runtime modules/assets and encryption/driver dependencies in
  wheel; no-dev installed app.core.database/app.main imports, actual template
  and static lookup from empty CWD; explicit writable LOG_DIR; no DB/provider
  or runtime/lifespan start; depends: none;
  owns: pyproject.toml, requirements.txt, tests/wheel_runtime_smoke.py,
  app/admin/setup.py, app/web/deps.py, app/core/logger.py, app/core/config.py,
  app/main.py (template path only), docs/CONFIGURATION.md and
  docs/validation/wheel-runtime-completeness.md;
  owner: remote Notion agent (fix/wheel-runtime-completeness); status: DONE.
  [PR #226](https://github.com/Starck43/social-media-ai/pull/226) merged at
  `c90ac9192103d59d4fcd54f14d050110537af72b` into dev after owner
  acceptance. Initial claim before implementation:
  `6bbc97f841beba30756b99ccebc288339980666c`, from dev
  `adf1d6fc79ea147863c4c2d7560349140ab2a6cf`.
  Historical packaging SHA `fa0c7e9701f13bdd68b29047b187431538c92e2d` passed
  366 Python + 91 asset byte checks in two no-dev venvs, five safe imports and
  Fernet; negative missing-file/dev-only-encryption controls failed as expected.
  Owner macOS 15.7.9 x86_64 / Python 3.12.6 review of that SHA passed the same
  packaging slice, namespace import, pip checks and cli --help, but found
  relative resource/log paths and undeclared greenlet/psycopg. Overall status
  was therefore BLOCKED, not acceptance of complete wheel usability.
  [Owner report](https://github.com/Starck43/social-media-ai/pull/226#issuecomment-6296837234)
  remains attributed, not a remote rerun or a report for the new SHA.
  Owner authorized continuation; claims published before scoped implementation:
  `ced26f99474c0038953040361ce930103bd53ffe` (dependencies),
  `779e4b6f00ea1efd925ac70a07039060a9abfa22` (resources/logging).
  Synchronized dev: `6fbe7a961dafc3b05fccd9cfe99f7652bdc6a33a`; owner's merged
  #225 work and foreign P10-T02/T03/T05 records are preserved. No new task IDs.
  Owner-relayed stale-worktree check: main.py dirty at detached 37eeaf7 is
  byte-identical to dev; its lifecycle changes are integrated in a5ba8c1
  (remote ancestry verified). No active net overlap is evidenced; no formal
  lane sign-off is claimed, and the old/dirty worktrees were not modified.
  Open PRs #216/#217/#220/#224/#227 have no target-path diff at recheck.
  Current implementation/test SHA: `60638e5b40a205c233941367c3edf41ea66758f8`.
  Both review gaps addressed: SQLAlchemy asyncio extra and psycopg runtime deps;
  module-relative static/templates; absolute LOG_DIR default ~/.social-media-ai/logs.
  main.py lifespan, maintenance loop and application factory AST unchanged.
  Exact-source remote self-run: Linux x86_64 / Python 3.13.14; offline wheel
  build exit 0; two new wheel-only venvs (requirements-constrained/unconstrained)
  each PASS 368 Python files + 94 assets (462 byte matches), real database/main
  imports, template/static lookups and Fernet. Default/explicit LOG_DIR give
  four successful child checks total, CWD unchanged; DB/runtime/external attempt
  counters all zero. Both pip checks exit 0; no dev packages. 9/9 negative
  controls fail as expected (exit 1), restored smoke passes. Scoped format,
  diff --check and lifecycle AST checks pass. Commands, hashes, versions,
  metadata-only platform.architecture shim, harness failures and limits:
  [current validation](https://github.com/Starck43/social-media-ai/blob/50c319c91cfe1e77f8dc244c29253e4f1a1df3c1/docs/validation/wheel-runtime-completeness.md).
  Owner handoff reports the target macOS check completed against the exact
  implementation SHA `60638e5b40a205c233941367c3edf41ea66758f8`; this is
  attributed owner evidence, not a local rerun or a reconstructed transcript.
  Independent dependency/resource/logging review found no blocking defect, and
  PR #226 is merged at `c90ac9192103d59d4fcd54f14d050110537af72b`.
  Full attribution, review reasoning and acceptance limits:
  [owner review](../audit/p10-t10-owner-review-2026-10-20.md).
  DONE covers wheel dependency closure and installed resource lookup only. No
  application startup, full regression, real DB, migration/reset, provider or
  deployment was run; those behaviors remain outside this task's acceptance.

- [x] P10-T13 — repair post-migration LLM CLI contracts; owner: codex; status: DONE. Accept: provider listing without removed default
  flag; shared local mock probe, no client/key/network setup; positive model ID
  and bounded prompt validation; explicit nonzero failures with no exception
  disclosure; real CLI dispatch fails closed without an authenticated payer.
  Synchronized base: `be07e515c4fa86e856af04faf2233c0680a57acd`.
  IDs P10-T11/P10-T12 were reserved by the pull requests that carried them and
  are now resolved in dev; those drafts and old dirty worktrees are preserved
  as history. No shared API/tool or billing changes.
  Implementation: provider default read removed; shared local mock restored;
  real CLI probes fail closed before catalog access (no authenticated payer).
  Remote self-run evidence: Python 3.13.14; pinned Typer/Rich and formatters;
  17 handler/shared-mock + 8 real parser tests passed, exit 0, no skips/xfails.
  Same tests fail on the clean synchronized dev baseline (both commands exit 1).
  No app startup, real DB/keys/providers, migration/reset or deployment.
  Deferred: existing-catalog ORM/CLI and zero-write/no-dispatch acceptance in
  the approved owner environment at the published PR SHA; coordinator review.
  Commands/limits: [CLI handoff](../CLI.md#llm-cli-validation-handoff).
  Coordinator source review on current dev `dea6e4d985f0d5962ad959e86d19429d85688d95`
  confirms the provider-list schema, shared mock probe, validation and
  fail-closed real-probe path are present, with focused CLI contract and parser
  tests in `tests/ai/`. No tests were run for this review; owner-environment
  catalog/zero-write checks and exact-current-SHA acceptance remain open.
  Owner-authorized coordinator closeout resumed on 2026-10-22; reconcile the
  surviving CLI contract with the active personal-catalog integration.
  Integration: reconcile #216's historical expected-failure probes and CLI
  warnings before merging both packages; do not erase pinned audit evidence.
  Personal-catalog boundary (Draft #229/P3-T93 — not merged): providers and
  models are user-owned, so `provider-list`/`model-list`/`model-test` read the
  acting user's catalog and need an explicit authenticated actor; without one
  they fail closed instead of listing a global fleet, and `--mock` resolves the
  same owner-scoped row rather than probing catalogue-wide IDs. `docs/CLI.md`
  §«LLM catalog and local model tests» and the name-based `AGENT_MODEL` lookup
  in `docs/CONFIGURATION.md` still describe the global behavior and must be
  updated with this contract when #229 lands.
  Accepted by coordinating Codex on 2026-10-24 at
  `0693c6985d28b274e955a97ac60a0fabeb680346`. Isolated CLI handler/parser
  checks: 40 passed through Kilo after the original OpenCode quota blocker.
  Real PostgreSQL catalog checks: 6 passed through the explicitly authorized
  Codex-subagent fallback, with two independent owners, foreign/no-actor
  refusal and mock zero-write/no-dispatch guards. The tested working candidate
  preceded the scoped commit; reviewed CLI and catalog dependency contents
  are unchanged in the accepted commit. Target was
  `localhost:5432/social_manager/test_schema`, revision `0109`; no schema or
  live-provider operation. The standalone shell has no authenticated actor
  and fails closed; this task does not add CLI sign-in or enable paid probes.
  The remaining paragraphs retain historical handoff boundaries.
  Owner-authorized worktree takeover resumed on 2026-10-24 at published dev
  `0f07e179f21c6a6e18a618169e700b64d15c50b5`. Personal catalog integration
  had landed; the prepared actor-scoped CLI adaptation and handler/parser/
  PostgreSQL suites were first reconciled as an uncommitted candidate.
  Primary OpenCode was then blocked by its old provider quota. The completed
  fallback checks and subsequent scoped commit are accepted above.

- [x] P10-T11 — audit post-migration CLI/API and embedded web-chat tools;
  accept: source-pinned interface inventory, prioritized verified findings,
  isolated no-DB reproductions and corrected interface documentation; no
  runtime/security acceptance implied. Depends: none; owns:
  docs/audit/2026-10-18-cli-api-chat.md on PR #216, scoped CLI/API/AGENT
  documentation and interface audit probes (reconcile the branch's root test
  into the current domain layout); owner: codex;
  status: DONE — accepted by coordinating Codex on 2026-10-24 at
  `94e9fff80b09c2d4a58cbc22ce0324f207ecf7c3`. Scope is the source-pinned
  inventory, current isolated reproductions and documentation; no general
  runtime/security acceptance. Canonical row restored from Draft
  [PR #216](https://github.com/Starck43/social-media-ai/pull/216), head
  `b9faf8dc68aecf350f2b656c9356a90cd1bf26fd`, after live head/ID verification
  on 2026-10-21. Original base `bd123b7` and 14 offline probes (4 passed,
  10 expected failures) remain historical evidence. Coordinator review at
  `f55abad30adbba9b411b31207dfa155423d490c6` supersedes the branch's READY
  claim: CLI and chat-model findings already fixed in dev must be refreshed
  before current-source acceptance; old expected failures are not current
  executable evidence. [Current audit](../audit/2026-10-21-ready-pr-audit.md).
  Chat-model findings must be refreshed against P3-T95 (Draft #229): selection
  becomes a numeric `agent_model_id` FK with no name fallback, so name-based
  selection expectations in the audit are historical, not current-source
  evidence.
  The owner transferred the remaining worktrees to Codex on 2026-10-24.
  The refreshed audit pins source `0693c698` and retains the source-pinned
  historical audit separately. Current isolated probes: 21 collected,
  17 passed, 4 strict xfailed through the owner-authorized Codex-subagent
  fallback; no app/DB/provider imports. The four expected failures document
  remaining capability/schema and credential-route gaps, not accepted runtime
  behavior. Source inventory and exact audit/probe commit were reviewed;
  the current CLI count is 29 literal declarations plus six generated task
  commands. Historical expected failures were preserved without presenting
  them as current passing behavior.

- [x] P10-T12 — preserve chat task configuration on partial updates and restore
  task listing; accept: omitted scenario/dates/targets remain unchanged;
  explicit scenario clearing; cron and next-run persistence agree;
  unavailable source/scenario inputs refused before writes; explicit source
  replacement; focused offline and sequential persistence/permission checks.
  Depends: P10-T11 audit context only, no merge dependency; owns:
  app/agent/toolset/tasks.py, chat task contract tests in the current domain
  layout, scoped docs/AGENT_TASKS.md; owner: codex;
  status: DONE. Accepted by coordinating Codex on 2026-10-24 at
  `6c581bf5ce2f1c36498f0f4c3d05c09e8b207bf5`. Final isolated-worktree
  acceptance through OpenCode build on
  `river/deepseek-ai/DeepSeek-V4.1-Flash-1017`: 41 collected, 41 passed,
  0 failed. This covers the actual manager/persistence boundary, partial
  fields, exact source links, rollback, scenario clearing/foreign and invalid
  ID refusal, schedule persistence and merging after the task lock. The run
  used the working candidate at `c56b699`; the scoped commit contains the
  same reviewed runtime and test contents. Target
  `localhost:5432/social_manager/test_schema`, revision `0109`; no migration,
  reset, seed or live provider. Earlier failed/mixed-checkout results below
  are superseded by this isolated run, not additional green evidence.
  Canonical row restored from Draft
  [PR #217](https://github.com/Starck43/social-media-ai/pull/217), head
  `c4161b5e67919d505e707f70ef18e6081cf11c81`, verified live 2026-10-21.
  Historical branch evidence: 27 new offline and 26 confirmation tests passed
  at base `be07e51` plus implementation; not a new run on current dev.
  Coordinator source review on current dev `dea6e4d985f0d5962ad959e86d19429d85688d95`
  confirms the task-list tool and omitted-field preservation are integrated,
  and cron changes update `next_run_at`. Remaining contract gaps are explicit
  scenario clearing (the `int | None` input cannot distinguish omission from
  clearing), source replacement (updates use additive `add_sources`), and
  validation/atomicity (source validation and link changes occur after the
  task row update). The PR-specific chat contract test file is absent from the
  current tree. Coordinator implementation/acceptance resumed on 2026-10-22:
  finish these contracts and focused offline plus sequential persistence/
  permission checks on integrated code. Preserve current task-owned collection
  and omitted-field behavior.
  Worktree takeover on 2026-10-24 reconciled the prepared implementation onto
  published base `0f07e179`: the update locks the current task before merging
  partial fields, task/link writes share one transaction, and the existing
  manager retains scenario/source validation and pending-job invalidation.
  That initial candidate was uncommitted; the isolated 41-case acceptance and
  scoped implementation commit are accepted above.
  Binding validation is manager-owned in P3-T94 (Draft #229): the parent row and
  every source/scenario row are locked and validated before writes, and a
  caller-supplied session is forwarded without a second commit. Reconcile with
  that implementation instead of adding a second validator, and keep PR #217's
  additive source update from bypassing atomic replacement.

- [x] P10-T15 — report scenario resets and warnings on default-model deletion;
  accept: `LLMModelManager.delete_with_default_reassignment` returns the number
  of agent scenarios/clients whose model FK was reset to NULL and the bounded
  warnings it already documents (for example "no model left for type X"), not
  the placeholder `scenarios_reset: None` / `warnings: []`; no new cascade
  rules, no schema or migration change. Found on 2026-10-24 while repairing
  tests after personal catalog ownership (`0109`): `llm_model_manager.py` still
  documents step 1–4 candidate ordering and the FK-reset count, but the body
  hardcodes both fields to empty. A delete therefore reports no evidence of the
  scenario bindings it just cleared, and the P8-T17 admin diagnostics cannot
  surface it. Depends: none; owns: `app/models/managers/llm_model_manager.py`
  delete path, focused manager tests; owner: opencode; status: DONE, accepted
  by coordinating Codex on 2026-10-24 at local dev
  `cb2793a6850641a87a51d5c69734bc0e6a54c111`. The reviewed implementation
  counts distinct scenarios, reports bounded replacement/workspace warnings,
  and makes no schema or migration changes. Focused manager coverage in this
  commit asserts scenario counts, warning content and the quiet replacement
  path. The task criteria and exact-SHA source diff were reviewed; no tests were
  run during this review.
  Do not re-implement the candidate ordering as a side effect: the replacement
  default is now chosen inside the owner's catalog (the previous
  `last_success_at` → `ai_analytics` name ordering is no longer the contract).
  Delivered: `scenarios_reset` counts distinct agent scenarios whose
  text/image/video model column is the deleted id (one row counted once) and
  `warnings` reports only what the delete could not repair — `no model left for
  type <type>` when a deleted default has no replacement, and
  `<n> workspace(s) lost their agent model` when `tenants.agent_model_id` was
  reset. Both counts are read before the delete in the same transaction; the
  second relies on `tenants` not being row-filtered (no RLS in migrations). No
  cascade, schema or migration change. The two `xfail(strict=True)` markers in
  `tests/models/test_llm_models_manager.py` turned into XPASS and were removed,
  together with four new cases (double reference in one scenario, foreign
  scenario ignored, workspace warning, silent ordinary path). Verified:
  `tests/models/test_llm_models_manager.py` 27 passed; combined with the
  repaired layout tests 95 passed, 0 failed. Not yet reviewed by the
  coordinator; DONE is set by Codex after review at this commit.

Related audit notes: admission activation → P2-T01; operator recovery → P4-T01;
C/D validation → P8-T03; DB binding → P7-T01; retention rollout → P6-T01.
Do not close a whole gate from this audit. PR #176 acceptance → P3-T01.
