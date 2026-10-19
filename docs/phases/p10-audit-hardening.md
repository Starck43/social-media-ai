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
  owner: unassigned; status: TODO

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
  sentinels absent; root readiness unchanged; depends: none; owner: unassigned;
  status: TODO

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

- [ ] P10-T14 — audit current ROADMAP implementation/acceptance drift;
  owner: remote Notion agent; status: READY. Accept: source-pinned
  cross-check of open rows, preserved owners/status/evidence, explicit ID and
  missing-phase discrepancies, isolated checks and dependency-aware priorities.
  No runtime fixes, foreign task closure, DB/providers or deployment.
  Evidence: [current-dev review](../audits/roadmap-current-dev-review.md), pinned
  dev `ddc9b99164c326a01a81e266fc1f26a15dd822cc`. New isolated observations:
  30 tests, 23 passed/7 expected failures (OPEN defects); existing green and
  broken fixtures and real offline wheel defects are reported separately.
  P10-T01/T02/T03 are implementation-present acceptance candidates, not closed.
  Task identity contradictions are flagged without changing foreign records.

Related audit notes: admission activation → P2-T01; operator recovery → P4-T01;
C/D validation → P8-T03; DB binding → P7-T01; retention rollout → P6-T01.
Do not close a whole gate from this audit. PR #176 acceptance → P3-T01.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
