# Active queue

Status/owner are authoritative here; scope, documents read and checks live only
in the linked task card. Open one card, not the entire docs tree.
Lifecycle: [COLLABORATION](docs/COLLABORATION.md#tasks). Legacy dialogue “—”
means not recorded; source recovery links remain in the card.

## [P2](docs/tasks/p2-usage-spend.md)

- [P2-T08](docs/tasks/p2-t08.md) corporate/shared billing accounts — **TODO** · product-owner · —
- [P2-T09](docs/tasks/p2-t09.md) production billing verification — **TODO** · unassigned · —

## [P3](docs/tasks/p3-identity-access.md)

## [P4](docs/tasks/p4-delivery.md)

- [P4-T01](docs/tasks/p4-t01.md) staging operator-recovery evidence — **BLOCKED** · unassigned · —

## [P5](docs/tasks/p5-ai-boundaries.md)

- [P5-T04](docs/tasks/p5-t04.md) remove web duplicate-analysis force bypasses and persist context-scoped success receipts — **BLOCKED** · codex-analysis-receipts · —

## [P6](docs/tasks/p6-lifecycle-privacy.md)

- [P6-T01](docs/tasks/p6-t01.md) Deploy-gated retention rollout — **BLOCKED** · product-owner · —
- [P6-T02](docs/tasks/p6-t02.md) One retention/export/offboarding contract — **TODO** · unassigned · —

## [P7](docs/tasks/p7-operations.md)

- [P7-T02](docs/tasks/p7-t02.md) One staging-backed operations proof — **BLOCKED** · unassigned · —

## [P8](docs/tasks/p8-first-value-ui.md)

- [P8-T10](docs/tasks/p8-t10.md) runtime freshness identity — **TODO** · unassigned · —
- [P8-T11](docs/tasks/p8-t11.md) incomplete-delivery aggregate signal — **TODO** · unassigned · —
- [P8-T13](docs/tasks/p8-t13.md) named support/escalation + pilot measures — **TODO** · unassigned · —
- [P8-T15](docs/tasks/p8-t15.md) Admin API connectivity, mock and real checks — **BLOCKED** · codex · —
- [P8-T17](docs/tasks/p8-t17.md) Model response diagnostics and consent — **BLOCKED** · codex · —
- [P8-T18](docs/tasks/p8-t18.md) Model tests in single-owner mode — **BLOCKED** · codex · —
- [P8-T20](docs/tasks/p8-t20.md) Provider cost evidence and diagnostic controls — **BLOCKED** · codex · —
- [P8-T29](docs/tasks/p8-t29.md) Universal social tasks, VK first — **BLOCKED** · codex-p8-t29 · [Q-5](docs/tasks/q-5.md)
- [P8-T33](docs/tasks/p8-t33.md) Additional VK search categories — **TODO** · unassigned · —
- [P8-T34](docs/tasks/p8-t34.md) Non-social resources and connector actions — **TODO** · unassigned · —
- [P8-T32](docs/tasks/p8-t32.md) collapsed inline sections and permission-aware links — **DONE** · opencode (fix/p8-t32-inline-row-links, `9534f9a`) · [PR #239 follow-up](https://github.com/Starck43/social-media-ai/pull/239#issuecomment-6404431470)

## [P9](docs/tasks/p9-safe-activation.md)

- [P9-T01](docs/tasks/p9-t01.md) one capability-specific activation contract — **TODO** · unassigned · —

## [P10](docs/tasks/p10-audit-hardening.md)

- [P10-T08](docs/tasks/p10-t08.md) Home pipeline permission gates — **TODO** · unassigned · —
- [P10-T09](docs/tasks/p10-t09.md) Truthful pipeline counts/labels — **TODO** · unassigned · —
- [P10-T16](docs/tasks/p10-t16.md) Scheduling model layout checks — **TODO** · codex · —
- [P10-T19](docs/tasks/p10-t19.md) Reconcile the failing security suites with the current authorization model — **READY** · opencode (fix/p10-t19-stale-security-suites, `b8eddfd`) · [merge `f838333`](https://github.com/Starck43/social-media-ai/pull/241#issuecomment-6409463751)
- [P10-T22](docs/tasks/p10-t22.md) Reconcile the dispatch-identity suite with the tariff receipt contract — **TODO** · unassigned · —

## Questions

- [Q-2](docs/tasks/q-2.md) VK source 2939 origin and missing media — **OPEN** · codex-vk-raw-audit · —
- [Q-5](docs/tasks/q-5.md) VK application profile for global search — **OPEN** · unassigned · —
- [Q-10](docs/tasks/q-10.md) Credential-disable route compatibility — **OPEN** · codex · —
- [Q-11](docs/tasks/q-11.md) Individual and company onboarding — **RESOLVED** · owner decision 2026-10-25 · [PR #238](https://github.com/Starck43/social-media-ai/pull/238) · ⚠️ ceiling fallout audited (1 real regression in `tests/security/test_vk_oauth.py`, 1 pre-existing suite break) — see [Q-11 follow-up](docs/tasks/q-11.md#follow-up-max_workspace_members-fallout-on-dev)
- [Q-12](docs/tasks/q-12.md) Self-service email password recovery for the public phase — **DEFERRED** · product-owner · pre-production: email confirmation first, then recovery

Choose one unclaimed dependency-ready task; P10 security P0 items have priority.
