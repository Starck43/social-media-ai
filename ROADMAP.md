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

- [ ] P1-T03 — distinguish a live queued Job from a task's historical queued receipt → [p1](docs/phases/p1-queue-execution.md) — unassigned — TODO
- [ ] P2-T08 — corporate/shared accounts (owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — product-owner — TODO
- [ ] P2-T09 — production billing verification (post-activation, owner-deferred) → [p2](docs/phases/p2-usage-spend.md) — unassigned — TODO
- [ ] P2-T12 — preserve cache usage and version complete cache tariffs → [p2](docs/phases/p2-usage-spend.md) — opencode (unknown-cost propagation) — READY
- [ ] P3-T01 — digest send permission parity acceptance on integrated code (#176 closed) → [p3](docs/phases/p3-identity-access.md) — remote Notion agent — TODO
- [ ] P3-T02 — close one actor/workspace/tool/write gap → [p3](docs/phases/p3-identity-access.md) — unassigned — TODO
- [ ] P4-T01 — staging operator-recovery evidence → [p4](docs/phases/p4-delivery.md) — unassigned — BLOCKED
- [ ] P5-T01 — close one typed-output/hostile-input/coverage gap → [p5](docs/phases/p5-ai-boundaries.md) — unassigned — TODO
- [ ] P5-T04 — persist analysis receipts to prevent duplicate model spend → [p5](docs/phases/p5-ai-boundaries.md) — owner: codex-analysis-receipts — BLOCKED
- [ ] P6-T01 — data-retention rollout (deploy gated) → [p6](docs/phases/p6-lifecycle-privacy.md) — product-owner — BLOCKED
- [ ] P6-T02 — one bounded retention/export/offboarding contract → [p6](docs/phases/p6-lifecycle-privacy.md) — unassigned — TODO
- [ ] P7-T01 — loopback-only DB binding (pre-production) → [p7](docs/phases/p7-operations.md) — product-owner — TODO
- [ ] P7-T02 — one staging-backed operation proof → [p7](docs/phases/p7-operations.md) — unassigned — BLOCKED
- [ ] P8-T08 — credential expiry/near-expiry signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T09 — queue backlog/age/quarantine operator signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T10 — scheduler/worker/listener freshness identity → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T11 — incomplete-delivery aggregate signal → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T13 — named support/escalation ownership + pilot measures → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P8-T15 — distinct admin API connectivity/mock/real request checks → [p8](docs/phases/p8-first-value-ui.md) — codex — BLOCKED
- [ ] P8-T17 — admin model response JSON and visible launch diagnostics → [p8](docs/phases/p8-first-value-ui.md) — codex — BLOCKED
- [ ] P8-T18 — allow model tests in single-owner accounting mode → [p8](docs/phases/p8-first-value-ui.md) — codex — BLOCKED
- [ ] P8-T20 — provider cost evidence and compact diagnostic controls → [p8](docs/phases/p8-first-value-ui.md) — codex — BLOCKED
- [ ] P8-T29 — agent paraphrase/search/compose tools for abstract "find and analyse" requests → [p8](docs/phases/p8-first-value-ui.md) — unassigned — TODO
- [ ] P9-T01 — one capability-specific activation contract → [p9](docs/phases/p9-safe-activation.md) — unassigned — TODO
- [ ] P9-T02 — platform-owner role `SUPERUSER` with a guarded assignment → [p9](docs/phases/p9-safe-activation.md) — codex — READY
- [ ] P9-T03 — owner-level gates built on the new role → [p9](docs/phases/p9-safe-activation.md) — codex — READY
- [ ] P9-T04 — read-only table registry for the platform owner → [p9](docs/phases/p9-safe-activation.md) — codex — READY
- [ ] P9-T05 — owner access documentation and regression guard → [p9](docs/phases/p9-safe-activation.md) — codex — READY
- [ ] P10-T01 — authorized password recovery (AUD-01, P0) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T03 — profile vs privilege update separation (AUD-03) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T04 — shared cookie-independent login protection (AUD-04) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T06 — safe provider-error envelopes (AUD-06) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T07 — digest scope/window/row consistency (AUD-07) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T08 — per-component home pipeline gating (AUD-08) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T09 — truthful pipeline counts/labels (AUD-09) → [p10](docs/phases/p10-audit-hardening.md) — unassigned — TODO
- [ ] P10-T15 — default-model deletion reports scenario resets and warnings → [p10](docs/phases/p10-audit-hardening.md) — opencode — READY
- [ ] P10-T16 — bring the scheduling layout test back to the real model layout → [p10](docs/phases/p10-audit-hardening.md) — codex — TODO

## Questions

- [ ] Q-10 — decide the credential-disable route contract: the composed
  endpoint is `PATCH /api/v1/credentials/credentials/{credential_id}/disable`
  (`entry.py` mounts `credentials.router` with `prefix="/credentials"` while the
  endpoint path already starts with `/credentials`). `docs/API.md` records that
  actual route and `tests/cli/test_interface_contract_audit.py` pins it, so
  nothing is broken today — the open decision is whether clients should keep
  the doubled segment or the endpoint should move to the single-segment
  `/api/v1/credentials/{credential_id}/disable` (alias vs breaking change).
  Context: [interface audit](docs/audits/2026-10-18-cli-api-chat.md);
  owner: codex; status: OPEN.

- [ ] Q-11 — how do clients (individuals and companies) enter the platform?
  Owner's current direction: anyone may register as an individual on the
  starting tier; corporate multi-account onboarding is undecided and stays
  separate from the platform-owner access work in P9-T02–P9-T05. Context:
  [P9 phase](docs/phases/p9-safe-activation.md); asked by: owner; status: OPEN

- [ ] Q-5 — which VK application profile/credentials should the deployment use
  for the search method family? The gate is the app profile, not the token: with
  both the service key and the personal user token, `newsfeed.search`,
  `groups.search`, `users.search`, `database.getCities` and
  `utils.resolveScreenName` answer `1051 "Method is not available for this
  profile type"` (app `54798450`, a web app with a localhost redirect), while
  `wall.get`, `users.get` and `groups.getById` work. The open decision is
  whether to move to a Standalone VK application (or reconfigure the profile)
  so the global search family opens; the vk.ru web search uses internal
  endpoints and is no evidence of API access. Until then, communities are
  imported from the operator's list (`python -m cli.main collection import`) and
  VK-wide mention discovery stays unavailable.
  Context: [VK search audit](docs/audits/2026-10-20-vk-search-and-task-2027.md);
  asked by: $local (opencode); status: OPEN

<!-- Q-<n> — question; context: <link/file>; asked by: <agent>; status: OPEN|RESOLVED(delete row) -->

- [ ] Q-9 — merged into Q-5 (same question: the app profile, not the token,
  gates the VK search method family). Row kept only until the next registry
  sweep so the old ID still resolves; delete then.
  Context: [VK search audit](docs/audits/2026-10-20-vk-search-and-task-2027.md);
  asked by: $local (opencode); status: OPEN

- [ ] Q-1 — compress `docs/validation/llm-budget-pg-runner.md` (807-line evidence ledger) to a short summary; its original gate condition (P2-T01 activation) is met — P2-T01 is DONE — so the only open point is whether to compress now or keep the ledger as a historical record; keep the example JSONs (pinned by `tests/test_budget_isolated_fixture_coherence.py`); context: docs/validation/; asked by: opencode; status: OPEN
- [ ] Q-2 — verify source 2939 VK post origin and missing media references against the original API response, if obtainable; context: [VK raw content audit](docs/audits/2026-10-18-vk-raw-content.md); owner: codex-vk-raw-audit; status: OPEN
