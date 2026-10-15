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

## Tasks

- [ ] P2-T01 — select, review and implement one bounded admission activation
  prerequisite: durable UTC-day baseline provisioning, reviewed bound
  production, receipt/outcome-to-settlement contract, or all-client/all-caller
  admission wiring; accept: chosen prerequisite implemented with focused
  evidence, admission invariants above preserved; depends: none;
  owns: per selection; owner: unassigned; status: TODO

- [ ] P2-T02 — operator-payer decision for admin connection tests;
  accept: owner decision recorded, then tests implemented per decision;
  depends: owner input; owner: owner; status: BLOCKED — awaiting owner decision

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
