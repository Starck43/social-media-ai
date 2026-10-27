# Active queue

Status/owner are authoritative here; scope, documents read and checks live only
in the linked task card. Open one card, not the entire docs tree.
Lifecycle: [COLLABORATION](docs/COLLABORATION.md#tasks); status set:
[state machine](docs/COLLABORATION.md#state-machine). Legacy dialogue “—”
means not recorded; source recovery links remain in the card.

## [P2](docs/phases/p2-usage-spend.md)

- [P2-T08](docs/tasks/p2-t08.md) corporate/shared billing accounts — **DEFERRED** · product-owner · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c`
- [P2-T09](docs/tasks/p2-t09.md) production billing verification — **TODO** · unassigned · —
- [P2-T15](docs/tasks/p2-t15.md) extensible capability entitlements foundation — **DEFERRED** (owner: tests/validation deferred to project end, 2026-10-27) · notion-ai · integrated 2026-10-27 as merge 67acbd53 (0117 re-parented onto 0116, [handoff + merge](https://github.com/Starck43/social-media-ai/pull/253#issuecomment-6428493023)); validation gates still open

## [P4](docs/phases/p4-delivery.md)

- [P4-T01](docs/tasks/p4-t01.md) staging operator-recovery evidence — **BLOCKED** · unassigned · —

## [P5](docs/phases/p5-ai-boundaries.md)

- [P5-T04](docs/tasks/p5-t04.md) remove web duplicate-analysis force bypasses and persist context-scoped success receipts — **BLOCKED** · codex-analysis-receipts · —

## [P6](docs/phases/p6-lifecycle-privacy.md)

- [P6-T01](docs/tasks/p6-t01.md) Deploy-gated retention rollout — **BLOCKED** · product-owner · —
## [P7](docs/phases/p7-operations.md)

- [P7-T02](docs/tasks/p7-t02.md) One staging-backed operations proof — **BLOCKED** · unassigned · —

## [P8](docs/phases/p8-first-value-ui.md)

- [P8-T15](docs/tasks/p8-t15.md) Admin API connectivity, mock and real checks — **BLOCKED** · codex · —
- [P8-T17](docs/tasks/p8-t17.md) Model response diagnostics and consent — **BLOCKED** · codex · —
- [P8-T18](docs/tasks/p8-t18.md) Model tests in single-owner mode — **BLOCKED** · codex · —
- [P8-T20](docs/tasks/p8-t20.md) Provider cost evidence and diagnostic controls — **BLOCKED** · codex · —
- [P8-T29](docs/tasks/p8-t29.md) Universal social tasks, VK first — **BLOCKED** · codex-p8-t29 · —
- [P8-T33](docs/tasks/p8-t33.md) Additional VK search categories — **TODO** · unassigned · —
- [P8-T34](docs/tasks/p8-t34.md) Non-social connector pilot — **DEFERRED** · product-owner · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c`

- [P8-T39](docs/tasks/p8-t39.md) Complete chat-first and standalone task journeys — **TODO** · unassigned · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c`
- [P8-T40](docs/tasks/p8-t40.md) Compose collection, analysis and digest schedules — **TODO** · unassigned · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c`
- [P8-T41](docs/tasks/p8-t41.md) Accept the first VK research-to-digest journey — **TODO** · unassigned · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c`

## [P11](docs/phases/p11-structure.md)

- [P11-T01](docs/tasks/p11-t01.md) Critical structural audit, refactoring plan and task-card reconciliation — **IN_PROGRESS** · notion-ai (remote executor) · [Notion chat](https://app.notion.com/chat?t=4066ba0cf6d880aba81600a9789b6feb)

## Questions

- [Q-2](docs/tasks/q-2.md) VK source 2939 origin and missing media — **OPEN** · codex-vk-raw-audit · —
- [Q-5](docs/tasks/q-5.md) VK application profile for global search — **OPEN** · unassigned · —
- [Q-10](docs/tasks/q-10.md) Credential-disable route compatibility — **OPEN** · codex · —
- [Q-12](docs/tasks/q-12.md) Self-service email password recovery for the public phase — **DEFERRED** · product-owner · pre-production: email confirmation first, then recovery
- P6-T05 audit follow-up: stale catalog assertion `tests/admin/test_admin_authorization.py::test_the_catalogue_reads_the_model_name_off_the_views` fails on dev (`KeyError: 'mark-read'` after the #246 notification-action removal) — **OPEN** · unassigned · [P6-T05 audit](docs/audit/p6-t05.md)
- P6-T05 audit follow-up: `tests/models/test_llm_models_manager.py::test_delete_reports_workspace_that_lost_its_agent_model` leaks its tenant (no cleanup) — **OPEN** · unassigned · [P6-T05 audit](docs/audit/p6-t05.md)

Choose one unclaimed dependency-ready task.
