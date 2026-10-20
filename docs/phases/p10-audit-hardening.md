# P10 — Source-audit hardening (2026-10-15 audit)

Goal: implement the runtime fixes from the source-pinned audit. Baseline
`7ba5fe4`; report, evidence and exclusive per-task file ownership:
[PR #196](https://github.com/Starck43/social-media-ai/pull/196)
(`docs/audits/2026-10-15-dev.md` on that branch). Risk-based source coverage,
not a full security/production certification. One bounded task per
implementation; serialize shared-file lanes and all DB acceptance under R10.

## Tasks

- [ ] P10-T01 (AUD-01, P0) — replace unauthenticated password mutation with
  authorized recovery; accept: account-bound, expiring, one-use recovery or
  approved operator-only reset; no anonymous hash mutation or password logs;
  depends: none; owner: unassigned; status: TODO

- [ ] P10-T02 (AUD-02, P1) — remove password hashes from HTTP user responses;
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


- [ ] P10-T03 (AUD-03, P1) — separate self-service profile updates from
  privilege updates; accept: profile updates cannot elevate authority; fresh
  privileged operator policy and endpoint/manager negative controls;
  depends: shared lane with P10-T02; owner: unassigned; status: TODO

- [ ] P10-T04 (AUD-04, P1) — shared, cookie-independent login protection;
  accept: normal failures counted; shared limits across surfaces/processes;
  valid dummy verifier and generic errors; depends: none; owner: unassigned;
  status: TODO

- [ ] P10-T05 (AUD-05, P2) — align legacy API DB probes with bounded redacted
  readiness; accept: bounded generic legacy probe failures; synthetic DB/DSN
  sentinels absent; root readiness unchanged; depends: none;
  owns: app/api/v1/entry.py, tests/test_legacy_api_db_probes.py and scoped
  docs/API.md health contract and docs/validation/legacy-api-db-probes.md;
  owner: remote Notion agent; status: READY.
  Synchronized base: `959a461410aa220cd1059c4e3ee59b55d5f79eaa`;
  active PRs #216/#217/#220 heads rechecked unchanged: no runtime/probe overlap;
  shared registry/phase edits remain task-scoped, foreign evidence preserved.
  Implementation/test SHA: `00f560e6236a43cd7f9ebeee9a0ab526a00d7618`.
  Remote self-run: 14 isolated tests passed, exit 0, no skips/xfails; final
  tests on the synchronized baseline: 8 failed / 6 passed, exit 1. Two-second
  cooperative ORM timeout, fixed legacy 500 detail, success/auth/cancellation
  preserved; root health source unchanged. Commands, environment, earlier failed
  test-harness run and limits: [validation](../validation/legacy-api-db-probes.md).
  READY is implementation review only, not DONE or DB/ASGI acceptance. Keep PR
  Draft pending coordinator review and required owner-local acceptance; no
  migration, reset, provider, runtime start or deployment authorized.

- [ ] P10-T06 (AUD-06, P1) — centralize safe provider-error envelopes without
  dropping accounting; accept: allowlisted failure envelopes/logs; no
  provider-body/prompt/token sentinel disclosure; attempt identity and unknown
  accounting preserved; depends: none; owner: unassigned; status: TODO

- [ ] P10-T07 (AUD-07, P1) — make digest scope, date window and row set
  consistent; accept: all sections use one source/scenario/window/base-row
  scope; boundary/rollup regressions and bounded reused reads; no delivery
  change; depends: none; owner: unassigned; status: TODO

- [ ] P10-T08 (AUD-08, P1) — gate home pipeline queries and links per data
  component; accept: denied component issues no query and shows no forbidden
  link; actual middleware and revocation cases; depends: none; owner:
  unassigned; status: TODO

- [ ] P10-T09 (AUD-09, P2) — make pipeline counts and activity labels
  truthful; accept: preview vs totals explicit, type-correct activity, literal
  or eligibility-backed staged counts; depends: P10-T08 (same files);
  owner: unassigned; status: TODO

- [ ] P10-T10 (AUD-10, P2) — distributable package runtime completeness;
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
  [owner review](../validation/p10-t10-owner-review-2026-10-20.md).
  DONE covers wheel dependency closure and installed resource lookup only. No
  application startup, full regression, real DB, migration/reset, provider or
  deployment was run; those behaviors remain outside this task's acceptance.

- [ ] P10-T13 — repair post-migration LLM CLI contracts; owner: remote Notion
  agent; status: BLOCKED. Accept: provider listing without removed default
  flag; shared local mock probe, no client/key/network setup; positive model ID
  and bounded prompt validation; explicit nonzero failures with no exception
  disclosure; real CLI dispatch fails closed without an authenticated payer.
  Synchronized base: `be07e515c4fa86e856af04faf2233c0680a57acd`.
  IDs P10-T11/P10-T12 are reserved by active PRs #216/#217; those drafts and
  old dirty worktrees are preserved. No shared API/tool or billing changes.
  Implementation: provider default read removed; shared local mock restored;
  real CLI probes fail closed before catalog access (no authenticated payer).
  Remote self-run evidence: Python 3.13.14; pinned Typer/Rich and formatters;
  17 handler/shared-mock + 8 real parser tests passed, exit 0, no skips/xfails.
  Same tests fail on the clean synchronized dev baseline (both commands exit 1).
  No app startup, real DB/keys/providers, migration/reset or deployment.
  Deferred: existing-catalog ORM/CLI and zero-write/no-dispatch acceptance in
  the approved owner environment at the published PR SHA; coordinator review.
  Commands/limits: [CLI handoff](../CLI.md#llm-cli-validation-handoff).
  Integration: reconcile #216's historical expected-failure probes and CLI
  warnings before merging both packages; do not erase pinned audit evidence.

Related audit notes: admission activation → P2-T01; operator recovery → P4-T01;
C/D validation → P8-T03; DB binding → P7-T01; retention rollout → P6-T01.
Do not close a whole gate from this audit. PR #176 acceptance → P3-T01.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
