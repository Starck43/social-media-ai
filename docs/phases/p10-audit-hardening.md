# P10 — Source-audit hardening (2026-10-15 audit)

Goal: implement the runtime fixes from the source-pinned audit. Baseline
`7ba5fe4`; report, evidence and exclusive per-task file ownership:
[PR #196](https://github.com/Starck43/social-media-ai/pull/196)
(`docs/audits/2026-10-15-dev.md` on that branch). Risk-based source coverage,
not a full security/production certification. One bounded task per
implementation; serialize shared-file lanes and all DB acceptance under R10.

## Tasks

- [ ] P10-T11 — audit post-migration CLI/API and embedded web-chat tools;
  accept: source-pinned interface inventory, prioritized verified findings,
  isolated no-DB reproductions and corrected interface documentation; no
  runtime/security acceptance implied. Depends: none; owns:
  docs/audits/2026-10-18-cli-api-chat.md, scoped CLI/API/AGENT documentation,
  tests/test_interface_audit_probes.py; owner: remote Notion agent;
  synchronized base: bd123b76e4e90829e2b935c05f6419ca36bd44be;
  evidence: [audit](../audits/2026-10-18-cli-api-chat.md), 14 offline probes
  (4 passed, 10 expected failures documenting open defects, exit 0); runtime
  unchanged and real-environment acceptance deferred to future fix tasks;
  status: READY.

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

Related audit notes: admission activation → P2-T01; operator recovery → P4-T01;
C/D validation → P8-T03; DB binding → P7-T01; retention rollout → P6-T01.
Do not close a whole gate from this audit. PR #176 acceptance → P3-T01.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
