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
  owner: remote Notion agent (fix/http-user-response-redaction); status: IN_PROGRESS.
  Claim before implementation from dev `aeb8c54d74a55710b5080fd0a980fbc0ecf36d8c`.
  Current UserInDB already excludes hashes (existing `eb6cad6`); this lane adds
  bounded HTTP/OpenAPI regression evidence and corrects stale response docs,
  not another runtime/schema or privilege redesign. P10-T03 remains unassigned
  and unchanged; no shared user.py/schema writes are planned. Open PRs
  #216/#217/#220/#224/#225/#226 checked; docs/API.md foreign hunks do not overlap
  the Users response section. Preserve other claims/evidence and Draft PRs.

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

- [ ] P10-T10 (AUD-10, P2) — distributable package includes runtime code and
  encryption dependency; accept: wheel contains nested runtime
  packages/assets; encryption dependency in runtime deps; isolated no-dev
  import/asset smoke, no DB/provider; depends: none; owner: unassigned;
  status: TODO

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
