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

- [ ] P2-T12 — preserve cache usage and version complete cache tariffs → [p2](docs/phases/p2-usage-spend.md) — unassigned — TODO
- [ ] P2-T13 — bind journal tariff lookup to provider/model identity → [p2](docs/phases/p2-usage-spend.md) — unassigned — TODO
- [ ] P2-T14 — enforce complete spend evidence in caps and user reports → [p2](docs/phases/p2-usage-spend.md) — unassigned — TODO

- [ ] P8-T25 — settings information layout and compact member access → [p8](docs/phases/p8-first-value-ui.md) — remote Notion agent — READY

- [ ] P8-T22 — unified social analysis tasks and operator web journey → [p8](docs/phases/p8-first-value-ui.md) — owner: codex-social-analysis — BLOCKED (persisted acceptance: missing test-schema receipts)

- [ ] P8-T20 — provider cost evidence and compact diagnostic controls → [p8](docs/phases/p8-first-value-ui.md) — remote Notion agent — BLOCKED

- [ ] P8-T18 — allow model tests in single-owner accounting mode → [p8](docs/phases/p8-first-value-ui.md) — remote Notion agent — BLOCKED

- [ ] P8-T17 — admin model response JSON and visible launch diagnostics → [p8](docs/phases/p8-first-value-ui.md) — remote Notion agent — BLOCKED

- [ ] P8-T16 — admin navigation, actor visibility and guarded catalog editing → [p8](docs/phases/p8-first-value-ui.md) — remote Notion agent — BLOCKED

- [ ] P8-T15 — distinct admin API connectivity/mock/real request checks → [p8](docs/phases/p8-first-value-ui.md) — remote Notion agent — BLOCKED

- [ ] P1-T01 — prove source admission on PostgreSQL (race/rollback/ASGI scope) → [p1](docs/phases/p1-queue-execution.md) — owner: codex — BLOCKED
- [ ] P1-T02 — next bounded recovery/admission gap → [p1](docs/phases/p1-queue-execution.md) — unassigned — TODO
- [ ] P2-T08 — corporate/shared accounts (owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — owner — TODO
- [ ] P2-T09 — production billing verification (post-activation, owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — unassigned — TODO
- [ ] P3-T01 — digest send permission parity acceptance (PR #176 Draft) → [p3](docs/phases/p3-identity-access.md) — remote Notion agent — TODO
- [ ] P3-T02 — close one actor/workspace/tool/write gap → [p3](docs/phases/p3-identity-access.md) — unassigned — TODO
- [ ] P4-T01 — staging operator-recovery evidence → [p4](docs/phases/p4-delivery.md) — unassigned — TODO
- [ ] P5-T01 — close one typed-output/hostile-input/coverage gap → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO
- [ ] P5-T04 — persist analysis receipts to prevent duplicate model spend → [p5](docs/phases/p5-ai-boundaries.md) — owner: codex-analysis-receipts — IN_PROGRESS
- [ ] P6-T01 — data-retention rollout (deploy gated) → [p6](docs/phases/p6-lifecycle-privacy.md) — owner — BLOCKED
- [ ] P6-T02 — one bounded retention/export/offboarding contract → [p6](docs/phases/p6-lifecycle-privacy.md) — unassigned — TODO
- [ ] P6-T03 — admin removal of unprocessed source content → [p6](docs/phases/p6-lifecycle-privacy.md) — owner: codex — IN_PROGRESS
- [ ] P6-T04 — preserve explicit task source scope when deleting its last source → [p6](docs/phases/p6-lifecycle-privacy.md) — unassigned — TODO
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
- [ ] P10-T05 — legacy API DB probe alignment (AUD-05) → [p10](docs/phases/p10-audit-hardening.md) — remote Notion agent — READY
- [ ] P10-T06 — safe provider-error envelopes (AUD-06) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T07 — digest scope/window/row consistency (AUD-07) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T08 — per-component home pipeline gating (AUD-08) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T09 — truthful pipeline counts/labels (AUD-09) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T10 — distributable package runtime completeness (AUD-10) → [p10](docs/phases/p10-audit-hardening.md) — remote Notion agent (fix/wheel-runtime-completeness) — READY (Draft PR #226; owner review pending)

- [ ] P10-T13 — LLM CLI provider listing and safe model-test contract → [p10](docs/phases/p10-audit-hardening.md) — remote Notion agent — BLOCKED


- [ ] P5-T05 — bounded-output map-reduce analysis pipeline (any model, any volume) → [p5](docs/phases/p5-ai-boundaries.md) — $local (opencode) — IN_PROGRESS

- [ ] P5-T06 — theme-chain similarity SQL fixed for json main_topics (was silently failing since Oct 1) → [p5](docs/phases/p5-ai-boundaries.md) — $local (opencode) — READY

- [ ] P5-T07 — text-only scenarios analyze to completion (media ignored per scenario models, text failover, daily-partial coverage merge) → [p5](docs/phases/p5-ai-boundaries.md) — $local (opencode) — READY

- [ ] P5-T08 — task selection scope (sources → tracked users → text) before the model call; `monitored_users` removed end to end → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO

- [ ] P5-T12 — VK author snapshot for reposts (`copy_history`) so tracked users match the carrier → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO

- [ ] P5-T13 — VK search sources, inflection-aware text scope, dry-run and 1000-row collection ceiling → [p5](docs/phases/p5-ai-boundaries.md) — $local (opencode) — READY

- [ ] P5-T09 — VK discovery sources: platform search collected in the same run (usernames for `source_add`) → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO

- [ ] P5-T10 — VK wall reposts search: author snapshots, selection on author, every repost staged once → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO

- [ ] P5-T11 — stable chain hint from the task and cross-source chain aggregation in a workspace → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO

- [ ] P8-T25 — agent paraphrase/search/compose tools for abstract "find and analyse" requests → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO

## Questions

- [ ] Q-5 — which VK application type/credentials should the deployment use for
  search methods? Context: `newsfeed.search`, `groups.search`, `users.search`
  and `database.getCities` answer `1051 "Method is not available for this
  profile type"` for both the personal VK ID token and `VK_SERVICE_KEY`, while
  `wall.get`/`users.get`/`groups.getById` work. Discovery of designer
  communities and VK-wide mentions is blocked until the app profile allows
  those methods (or a different app is configured).
  Context: [VK search audit](docs/audits/2026-10-20-vk-search-and-task-2027.md);
  asked by: $local (opencode); status: OPEN

- [ ] Q-3 — is a discovered VK search result a Source row, or a separate
  candidate artefact that the operator confirms? Context: discovery of
  city/industry populations can return hundreds of walls; creating them all as
  sources with an hourly schedule multiplies collection jobs, rate limits and
  spend. Proposed default in P5-T09/P8-T25: candidate artefact + capped confirm.
  Asked by: $local (opencode); status: OPEN

- [x] Q-4 — one-off data decision for the `monitored_users` removal (P5-T08
  step 4). Owner decision (2026-10-19): **drop the stored values outright**, no
  conversion — the owner re-enters selections in new tasks. Implemented as
  migration `0105` (`payload`/`params` with the key stripped; downgrade is a
  documented no-op); a pre-migration snapshot of the two affected sources is in
  `.agents/tmp/monitored_users_backup_0105.json`. Applied locally.
  Asked by: $local (opencode); status: RESOLVED


<!-- Q-<n> — question; context: <link/file>; asked by: <agent>; status: OPEN|RESOLVED(delete row) -->

- [ ] Q-1 — compress `docs/validation/llm-budget-pg-runner.md` (807-line evidence ledger) to a short summary after P2-T01 activation completes; keep the example JSONs (pinned by `tests/test_budget_isolated_fixture_coherence.py`); context: docs/validation/; asked by: opencode; status: OPEN
- [ ] Q-2 — verify source 2939 VK post origin and missing media references against the original API response, if obtainable; context: [VK raw content audit](docs/audits/2026-10-18-vk-raw-content.md); owner: codex-vk-raw-audit; status: OPEN
