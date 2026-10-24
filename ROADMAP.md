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

- [ ] P2-T12 — preserve cache usage and version complete cache tariffs → [p2](docs/phases/p2-usage-spend.md) — opencode (unknown-cost propagation) — IN_PROGRESS

- [ ] P8-T20 — provider cost evidence and compact diagnostic controls → [p8](docs/phases/p8-first-value-ui.md) — codex — IN_PROGRESS

- [ ] P8-T18 — allow model tests in single-owner accounting mode → [p8](docs/phases/p8-first-value-ui.md) — codex — IN_PROGRESS

- [ ] P8-T17 — admin model response JSON and visible launch diagnostics → [p8](docs/phases/p8-first-value-ui.md) — codex — IN_PROGRESS

- [ ] P8-T16 — admin navigation, actor visibility and guarded catalog editing → [p8](docs/phases/p8-first-value-ui.md) — codex — IN_PROGRESS

- [ ] P8-T15 — distinct admin API connectivity/mock/real request checks → [p8](docs/phases/p8-first-value-ui.md) — codex — IN_PROGRESS

- [ ] P1-T03 — distinguish a live queued Job from a task's historical queued receipt → [p1](docs/phases/p1-queue-execution.md) — unassigned — TODO
- [ ] P2-T08 — corporate/shared accounts (owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — owner — TODO
- [ ] P2-T09 — production billing verification (post-activation, owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — unassigned — TODO
- [ ] P3-T01 — digest send permission parity acceptance on integrated code (#176 closed) → [p3](docs/phases/p3-identity-access.md) — remote Notion agent — TODO
- [ ] P3-T02 — close one actor/workspace/tool/write gap → [p3](docs/phases/p3-identity-access.md) — unassigned — TODO
- [ ] P4-T01 — staging operator-recovery evidence → [p4](docs/phases/p4-delivery.md) — unassigned — TODO
- [ ] P5-T01 — close one typed-output/hostile-input/coverage gap → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO
- [ ] P5-T04 — persist analysis receipts to prevent duplicate model spend → [p5](docs/phases/p5-ai-boundaries.md) — owner: codex-analysis-receipts — IN_PROGRESS
- [ ] P6-T01 — data-retention rollout (deploy gated) → [p6](docs/phases/p6-lifecycle-privacy.md) — owner — BLOCKED
- [ ] P6-T02 — one bounded retention/export/offboarding contract → [p6](docs/phases/p6-lifecycle-privacy.md) — unassigned — TODO
- [ ] P7-T01 — loopback-only DB binding (pre-production) → [p7](docs/phases/p7-operations.md) — owner — TODO
- [ ] P7-T02 — one staging-backed operation proof → [p7](docs/phases/p7-operations.md) — unassigned — TODO
- [ ] P8-T08 — credential expiry/near-expiry signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T09 — queue backlog/age/quarantine operator signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T10 — scheduler/worker/listener freshness identity → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T11 — incomplete-delivery aggregate signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T13 — named support/escalation ownership + pilot measures → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P9-T01 — one capability-specific activation contract → [p9](docs/phases/p9-safe-activation.md) — unassigned — TODO
- [ ] P9-T02 — platform-owner role SUPERUSER with guarded assignment → [p9](docs/phases/p9-safe-activation.md) — unassigned — TODO
- [ ] P9-T03 — owner-level gates on the superuser role → [p9](docs/phases/p9-safe-activation.md) — unassigned — TODO
- [ ] P9-T04 — read-only table registry for the platform owner → [p9](docs/phases/p9-safe-activation.md) — unassigned — TODO
- [ ] P9-T05 — owner access documentation and regression guard → [p9](docs/phases/p9-safe-activation.md) — unassigned — TODO
- [ ] P10-T01 — authorized password recovery (AUD-01, P0) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T03 — profile vs privilege update separation (AUD-03) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T04 — shared cookie-independent login protection (AUD-04) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T06 — safe provider-error envelopes (AUD-06) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T07 — digest scope/window/row consistency (AUD-07) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T08 — per-component home pipeline gating (AUD-08) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T09 — truthful pipeline counts/labels (AUD-09) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO

- [ ] P10-T13 — LLM CLI provider listing and safe model-test contract → [p10](docs/phases/p10-audit-hardening.md) — codex — IN_PROGRESS

- [ ] P10-T11 — refresh source-pinned CLI/API/chat audit and reproductions (#216) → [p10](docs/phases/p10-audit-hardening.md) — remote Notion agent — IN_PROGRESS
- [ ] P10-T12 — preserve partial chat task updates and restore task listing (#217) → [p10](docs/phases/p10-audit-hardening.md) — codex — IN_PROGRESS

- [ ] P10-T15 — default-model deletion reports scenario resets and warnings → [p10](docs/phases/p10-audit-hardening.md) — opencode — READY



- [ ] P8-T29 — agent paraphrase/search/compose tools for abstract "find and analyse" requests → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO

## Questions

- [ ] Q-11 — how do clients (individuals and companies) enter the platform?
  Owner's current direction: anyone may register as an individual on the
  starting tier; corporate multi-account onboarding is undecided and stays
  separate from the platform-owner access work in P9-T02–P9-T05. Context:
  [P9 phase](docs/phases/p9-safe-activation.md); asked by: owner; status: OPEN

- [ ] Q-5 — which VK application type/credentials should the deployment use for
  search methods? Context: `newsfeed.search`, `groups.search`, `users.search`
  and `database.getCities` answer `1051 "Method is not available for this
  profile type"` for both the personal VK ID token and `VK_SERVICE_KEY`, while
  `wall.get`/`users.get`/`groups.getById` work. Discovery of designer
  communities and VK-wide mentions is blocked until the app profile allows
  those methods (or a different app is configured).
  Context: [VK search audit](docs/audits/2026-10-20-vk-search-and-task-2027.md);
  asked by: $local (opencode); status: OPEN

<!-- Q-<n> — question; context: <link/file>; asked by: <agent>; status: OPEN|RESOLVED(delete row) -->

- [ ] Q-9 — will the deployment move to a Standalone VK application so the
  global search family opens? Context: a read-only probe answered 1051 for
  `newsfeed.search`, `groups.search`, `users.search`, `database.getCities` and
  `utils.resolveScreenName` with BOTH the service key and the personal user
  token, while `users.get`, `groups.getById` and `wall.get` work — the gate is
  the app profile (app `54798450`, a web app with a localhost redirect), not the
  token. The vk.ru web search uses internal endpoints and is no evidence of
  API access. Until it opens, communities are imported from the operator's
  list (`python -m cli.main collection import`).
  Context: [VK search audit](docs/audits/2026-10-20-vk-search-and-task-2027.md);
  asked by: $local (opencode); status: OPEN

- [ ] Q-1 — compress `docs/validation/llm-budget-pg-runner.md` (807-line evidence ledger) to a short summary after P2-T01 activation completes; keep the example JSONs (pinned by `tests/test_budget_isolated_fixture_coherence.py`); context: docs/validation/; asked by: opencode; status: OPEN
- [ ] Q-2 — verify source 2939 VK post origin and missing media references against the original API response, if obtainable; context: [VK raw content audit](docs/audits/2026-10-18-vk-raw-content.md); owner: codex-vk-raw-audit; status: OPEN
