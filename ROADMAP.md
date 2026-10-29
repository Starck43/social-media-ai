# Active queue

Status/owner, execution priority and complexity are authoritative here; scope,
dependencies, documents read and checks live in each linked task card.
Lifecycle: [COLLABORATION](docs/COLLABORATION.md#tasks); status transitions:
[state machine](docs/COLLABORATION.md#state-machine). Legacy dialogue “—” means
not recorded; source recovery links remain in the card.

## Priority and complexity

- `P0`: review/correctness/access/data-cleanup blockers; resolve before building on the affected behavior.
- `P1`: safety foundations, first complete product journey and its release evidence.
- `P2`: broader interface hardening and maintainability after the required seams exist.
- `P3`: demand-led preparation and future extension; not a pilot prerequisite.
- `C1`: small isolated documentation/assertion change; low coupling.
- `C2`: bounded single-subsystem work or review; focused compatibility checks.
- `C3`: multi-file subsystem change/integration or external-evidence uncertainty.
- `C4`: cross-interface, persistence/concurrency/security work with substantial verification.
- `C5`: broad cross-cutting/high-uncertainty delivery; plan reviewed slices before implementation.

These are provisional engineering estimates, not elapsed-time promises or model
selection. Complexity includes the **remaining** acceptance/evidence work; review
and registration estimates are not the size of the whole audited system.
Order is global across phases: P0 → P1 → P2 → P3, then row order within each
band. Dependencies, ownership and environmental gates override the next row:
review READY work; fix BLOCKED work only within its owning lane; claim the first
unassigned TODO whose required predecessors/permissions are satisfied. Skip a
gated item, do not idle or mark it accepted. TODO can have unfulfilled predecessors.
Explicit DEFERRED rows are parked separately and never auto-resumed by priority.
Historical statuses and owner-selected deferrals are unchanged by this ranking.

## Execution queue

### Priority P0



- [Q-5](docs/tasks/q-5.md) VK application profile for global search — **OPEN** · owner (VK support requested app/access/scenario/site/community details; method/profile availability remains unconfirmed) · — · `P0` · `C3`

### Priority P1

- [P8-T29](docs/tasks/p8-t29.md) Universal social tasks, VK first — **BLOCKED** · codex-p8-t29 · — · `P1` · `C4`
- [P8-T41](docs/tasks/p8-t41.md) Accept the first VK research-to-digest journey — **DEFERRED** (owner: park after local mock/DB increment until runtime journey and bounded live VK evidence, 2026-10-29) · notion-ai-p8-t41 · [Closeout](https://github.com/Starck43/social-media-ai/pull/279#issuecomment-6492303448) · resume `dev` at `295268d9` · `P1` · `C4`
- [P4-T01](docs/tasks/p4-t01.md) staging operator-recovery evidence — **BLOCKED** · unassigned · — · `P1` · `C3`
- [P7-T02](docs/tasks/p7-t02.md) One staging-backed operations proof — **BLOCKED** · unassigned · — · `P1` · `C4`
- [P6-T01](docs/tasks/p6-t01.md) Deploy-gated retention rollout — **BLOCKED** · product-owner · — · `P1` · `C3`

### Priority P2

- [Q-10](docs/tasks/q-10.md) Credential-disable route compatibility — **OPEN** · codex · — · `P2` · `C2`
- [P11-T26](docs/tasks/p11-t26.md) Remove legacy model import shims — **IN_PROGRESS** · notion-ai · [Execution](https://app.notion.com/chat?t=408439228587804093ec00a909303868) · `P2` · `C3`
- [P8-T33](docs/tasks/p8-t33.md) Additional VK search categories — **TODO** · unassigned · — · `P2` · `C4`
- [P2-T09](docs/tasks/p2-t09.md) production billing verification — **TODO** · unassigned · — · `P2` · `C4`
- [Q-2](docs/tasks/q-2.md) VK source 2939 origin and missing media — **OPEN** · codex-vk-raw-audit · — · `P2` · `C3`

### Priority P3

- [P11-T18](docs/tasks/p11-t18.md) Typed connector and job extension seams — **IN_PROGRESS** · notion-ai · [Execution](https://app.notion.com/chat?t=408895d2d5a18081942600a990c14a8a) · `P3` · `C4`
- [P11-T19](docs/tasks/p11-t19.md) Capacity telemetry and reproducible scale evidence — **IN_PROGRESS** · notion-ai · [Execution](https://app.notion.com/chat?t=408895d2d5a18081942600a990c14a8a) · `P3` · `C4`

## Deferred by explicit owner decision

- [P2-T08](docs/tasks/p2-t08.md) corporate/shared billing accounts — **DEFERRED** · product-owner · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c` · `P3` · `C4`
- [P2-T15](docs/tasks/p2-t15.md) extensible capability entitlements foundation — **DEFERRED** (owner: tests/validation deferred to project end, 2026-10-27) · notion-ai · integrated 2026-10-27 as merge 67acbd53 (0117 re-parented onto 0116, [handoff + merge](https://github.com/Starck43/social-media-ai/pull/253#issuecomment-6428493023)); validation gates still open · `P3` · `C4`
- [P8-T34](docs/tasks/p8-t34.md) Non-social connector pilot — **DEFERRED** · product-owner · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c` · `P3` · `C4`
- [Q-12](docs/tasks/q-12.md) Self-service email password recovery for the public phase — **DEFERRED** · product-owner · pre-production: email confirmation first, then recovery · `P3` · `C4`

## Phase navigation

Stable anchors below preserve inbound task-card links. The queue above is the
only status/priority list; phases contain numbered plans and parent relations.

## [P2](docs/phases/p2-usage-spend.md)

Use the P2 cards in the execution queue or the linked parent plan.

## [P4](docs/phases/p4-delivery.md)

Use the P4 cards in the execution queue or the linked parent plan.

## [P5](docs/phases/p5-ai-boundaries.md)

Use the P5 cards in the execution queue or the linked parent plan.

## [P6](docs/phases/p6-lifecycle-privacy.md)

Use the P6 cards in the execution queue or the linked parent plan.

## [P7](docs/phases/p7-operations.md)

Use the P7 cards in the execution queue or the linked parent plan.

## [P8](docs/phases/p8-first-value-ui.md)

Use the P8 cards in the execution queue or the linked parent plan.

## [P11](docs/phases/p11-structure.md)

Use the P11 cards in the execution queue or the linked parent plan.

## Questions

Q-2/Q-5/Q-10 are ordered in the execution queue; Q-12 remains owner-deferred.
The P6-T05 audit's two test follow-ups were accepted under [P6-T06](docs/audit/p6-t06.md); the original audit remains historical evidence.
