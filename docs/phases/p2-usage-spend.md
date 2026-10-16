# P2 — Complete usage and spend (gate PRD-03)

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

- [ ] P2-T01 — model personal billing account identity and fixed task payer
  binding; changing payer requires new payer consent; accept: personal billing
  account identity, fixed task payer for manual and background runs, consent
  on change, admission invariants above preserved;
  depends: none; owns: billing/payer modeling and application;
  owner: billing-agent; status: IN_PROGRESS

- [ ] P2-T02 — admin connection-test payer: paid probes charged to the
  launching user's personal account; accept: decision enforced for admin paid
  probes, requires platform admin rights; depends: P2-T01;
  owner: billing-agent; status: IN_PROGRESS

- [ ] P2-T03 — explicit compatible transition and mapping for legacy finance
  records and tasks; accept: ambiguous records are never automatically
  reassigned; depends: P2-T01; owner: billing-agent; status: IN_PROGRESS

- [ ] P2-T04 — atomic idempotent settlement/reconciliation;
  accept: uncertain obligations preserved after ambiguous outcomes;
  depends: P2-T01, P2-T03; owner: billing-agent; status: IN_PROGRESS

- [ ] P2-T05 — provision UTC daily baselines; accept: reviewed upper bounds
  for paid attempts; depends: P2-T04; owner: billing-agent; status: IN_PROGRESS

- [ ] P2-T06 — wire admission through all provider clients and callers;
  accept: no fallback around admission denial; depends: P2-T02–P2-T05;
  owner: billing-agent; status: IN_PROGRESS

- [ ] P2-T07 — focused PG/ASGI acceptance and cutover;
  accept: per acceptance recorded via R10; depends: P2-T01–P2-T06
  prerequisites; owner: codex; status: IN_PROGRESS; evidence: integrated no-DB
  pytest 77/77 PASS; Alembic 0103 current in `public` and `test_schema`.
  Owner-authorized disposable `test_schema` recreated without backup and
  seeded by canonical setup. Post-0103 sequential acceptance: 15/15 PASS
  (5 personal-ledger and 10 notification/settings cases). OpenCode and Kilo
  were unavailable; owner-authorized coordinator fallback ran the same scope.
  P2-T01–T06 remain IN_PROGRESS, so cutover prerequisites are not yet accepted.

- [ ] P2-T08 — corporate/shared billing accounts; accept: keep clear deferred
  status, no implementation now; depends: owner deferral;
  owner: owner; status: TODO — deferred by owner

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
