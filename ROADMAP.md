# Open task registry

Live list of open tasks and questions. Full task text, acceptance and evidence
live in the phase files under [docs/phases/](docs/phases/README.md); rows here
carry only ID, one-line title, link, owner and status — never duplicate the
task text.

Rules for every agent (Codex, OpenCode, Kilo, remote):

- Starting work: claim a row here (owner + `IN_PROGRESS`) and in the phase file.
- New question that needs separate elaboration (found during work or during
  dialogue with the owner): add a `Q-<n>` row below and, if it turns into real
  work, a task in the appropriate phase file.
- Resolving a row: set `DONE`/`DROPPED` in the phase file with evidence, then
  DELETE the row here. Phase closure requires Codex approval in the phase file.
- Statuses: `TODO`, `IN_PROGRESS`, `BLOCKED`, `READY`, `DONE`, `DROPPED`
  (see docs/phases/README.md).

## Tasks

- [ ] P1-T01 — prove source admission on PostgreSQL (race/rollback/ASGI scope) → [p1](docs/phases/p1-queue-execution.md) — owner: codex — BLOCKED
- [ ] P1-T02 — next bounded recovery/admission gap → [p1](docs/phases/p1-queue-execution.md) — unassigned — TODO
- [ ] P2-T04 — atomic idempotent settlement/reconciliation → [p2](docs/phases/p2-usage-spend.md) — billing-agent — IN_PROGRESS
- [ ] P2-T06 — admission wiring through all provider clients/callers → [p2](docs/phases/p2-usage-spend.md) — billing-agent — IN_PROGRESS
- [ ] P2-T07 — focused PG/ASGI acceptance and cutover → [p2](docs/phases/p2-usage-spend.md) — codex — IN_PROGRESS
- [ ] P2-T08 — corporate/shared accounts (owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — owner — TODO
- [ ] P2-T09 — production billing verification (post-activation, owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — unassigned — TODO
- [ ] P3-T01 — digest send permission parity acceptance (PR #176 Draft) → [p3](docs/phases/p3-identity-access.md) — remote Notion agent — TODO
- [ ] P3-T02 — close one actor/workspace/tool/write gap → [p3](docs/phases/p3-identity-access.md) — unassigned — TODO
- [ ] P4-T01 — staging operator-recovery evidence → [p4](docs/phases/p4-delivery.md) — unassigned — TODO
- [ ] P5-T01 — close one typed-output/hostile-input/coverage gap → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO
- [ ] P6-T01 — data-retention rollout (deploy gated) → [p6](docs/phases/p6-lifecycle-privacy.md) — owner — BLOCKED
- [ ] P6-T02 — one bounded retention/export/offboarding contract → [p6](docs/phases/p6-lifecycle-privacy.md) — unassigned — TODO
- [ ] P7-T01 — loopback-only DB binding (pre-production) → [p7](docs/phases/p7-operations.md) — owner — TODO
- [ ] P7-T02 — one staging-backed operation proof → [p7](docs/phases/p7-operations.md) — unassigned — TODO
- [ ] P8-T07 — operator-visible stale-source observable → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T08 — credential expiry/near-expiry signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T09 — queue backlog/age/quarantine operator signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T10 — scheduler/worker/listener freshness identity → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T11 — incomplete-delivery aggregate signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T12 — unknown-price/spend exposure signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T13 — named support/escalation ownership + pilot measures → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P9-T01 — one capability-specific activation contract → [p9](docs/phases/p9-safe-activation.md) — unassigned — TODO
- [ ] P10-T01 — authorized password recovery (AUD-01, P0) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T02 — no password hashes in HTTP responses (AUD-02) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T03 — profile vs privilege update separation (AUD-03) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T04 — shared cookie-independent login protection (AUD-04) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T05 — legacy API DB probe alignment (AUD-05) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T06 — safe provider-error envelopes (AUD-06) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T07 — digest scope/window/row consistency (AUD-07) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T08 — per-component home pipeline gating (AUD-08) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T09 — truthful pipeline counts/labels (AUD-09) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T10 — distributable package runtime completeness (AUD-10) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO

## Questions

<!-- Q-<n> — question; context: <link/file>; asked by: <agent>; status: OPEN|RESOLVED(delete row) -->

- [ ] Q-1 — compress `docs/validation/llm-budget-pg-runner.md` (807-line evidence ledger) to a short summary after P2-T01 activation completes; keep the example JSONs (pinned by `tests/test_budget_isolated_fixture_coherence.py`); context: docs/validation/; asked by: opencode; status: OPEN
