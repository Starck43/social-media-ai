# Active queue

Status/owner, execution priority and complexity are authoritative here; scope,
dependencies, documents read and checks live in each linked task card.
Lifecycle: [COLLABORATION](docs/process/COLLABORATION.md#tasks); status transitions:
[state machine](docs/process/COLLABORATION.md#state-machine). Legacy dialogue “—” means
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






- [Q-5](docs/tasks/q-5.md) VK application profile for global search — **OPEN** · owner (VK support requested app/access/scenario/site/community details; method/profile availability remains unconfirmed — **this is the only gate left for the mocked P8-T33/T46/T47/T48 categories**) · — · `P0` · `C3`

- [P21-T13](docs/tasks/p21-t13.md) Unified model catalog, plan grants and generation policy — **IN_PROGRESS** · opencode-p21-t13 · increment A now (dormant catalog storage, plan grants, owner-only management; no `app/services/ai/**`, no activation); increment B after P21-T12 merges · local DB runner `opencode-local` · dialogue `opencode-p21-t13-20261114` · `P1` · `C4`
- [P21-T14](docs/tasks/p21-t14.md) Provider rate limits and bounded backpressure — **TODO** · unassigned · depends on P21-T12/P21-T13 · `P1` · `C4`
- [P21-T15](docs/tasks/p21-t15.md) Atomic LLM admission and quota reservation wiring — **TODO** · unassigned · depends on P21-T14/P2-T17 allowance policy; no cutover · `P1` · `C4`
- [P21-T16](docs/tasks/p21-t16.md) LLM settlement, reconciliation and liability closure — **TODO** · unassigned · depends on P21-T15; integrated mocked phase checks; feeds P2-T09 · `P1` · `C4`

### Priority P1


- [P8-T29](docs/tasks/p8-t29.md) Universal social tasks, VK first — **BLOCKED** · codex-p8-t29 · — · `P1` · `C4`
- [P8-T41](docs/tasks/p8-t41.md) Accept the first VK research-to-digest journey — **DEFERRED** (owner: park after local mock/DB increment until runtime journey and bounded live VK evidence, 2026-10-29) · notion-ai-p8-t41 · [Closeout](https://github.com/Starck43/social-media-ai/pull/279#issuecomment-6492303448) · resume `dev` at `295268d9` · `P1` · `C4`
- [P4-T01](docs/tasks/p4-t01.md) staging operator-recovery evidence — **BLOCKED** · unassigned · — · `P1` · `C3`
- [P7-T02](docs/tasks/p7-t02.md) One staging-backed operations proof — **BLOCKED** · unassigned · — · `P1` · `C4`
- [P6-T01](docs/tasks/p6-t01.md) Deploy-gated retention rollout — **BLOCKED** · product-owner · — · `P1` · `C3`

### Priority P2





- [P2-T09](docs/tasks/p2-t09.md) production billing verification — **TODO** (gate: production activation/cutover; not claimable before it) · unassigned · — · `P2` · `C4`
- [Q-2](docs/tasks/q-2.md) VK source 2939 origin and missing media — **OPEN** · codex-vk-raw-audit · — · `P2` · `C3`

- [P12-T12](docs/tasks/p12-t12.md) File workspace: contract ownership and gated remainder after the accepted increment (byte authority/original-open with deferred P8-T34, local-only execution with P18, mutations per D21) — **TODO** (gate: owner activation / P8-T34 resume / consumer contract request; not claimable before it; re-scoped 2026-11-11, increment accepted 2026-11-03 `6433ae6a`/`48caea61`) · unassigned · [re-scope record](docs/tasks/p12-t12.md#re-scoped-remaining-roadmap-2026-11-11) · [increment audit](docs/audit/p12/p12-t12-increment.md) · `P2` · `C5`
- [P8-T34](docs/tasks/p8-t34.md) Yandex Disk read-only file pilot — **DEFERRED** (owner, 2026-11-04: VK full method access is unresolved, so the exact-byte version authority and native original-open cannot be closed) · resume: draft [PR #313](https://github.com/Starck43/social-media-ai/pull/313) · [audit](docs/audit/p8/p8-t34.md) · `P2` · `C4`
- [P12-T15](docs/tasks/p12-t15.md) User-managed folder permissions and safe file actions — **TODO** · unassigned · D21 ownership settled by the [P12-T12 re-scope](docs/tasks/p12-t12.md#re-scoped-remaining-roadmap-2026-11-11) 2026-11-11: extends P12-T12 grant management, never a second one; the mutation stage needs its own scoped authorization · [dialogue](https://app.notion.com/chat?t=40c071f39db6802e935c00a98e78df77) · `P2` · `C4`

Business modules P13–P19 ([adapted TZ and review](docs/product/BUSINESS_MODULES.md)); bands set by owner decision D1 ([Q-18](docs/audit/q/q-18-owner-decisions.md)):

- [P14-T08](docs/tasks/p14-t08.md) Bitrix24 on-premise authorization form on top of the P14-T09 seam — **DEFERRED** (owner, 2026-11-04: no on-premise customer; resumes with P14-T09) · `P2` · `C3`


### Priority P3

- [P20-T05](docs/tasks/p20-t05.md) Review-gated isolated browser execution pilot — **TODO** · unassigned · [request](https://app.notion.com/chat?t=40fa7457748980d094a100a930619250) · `P3` · `C5`

- [P17-T05](docs/tasks/p17-t05.md) 1C role-based access policy and audit views — **IN_PROGRESS** · opencode-p17-t05 · remote lane, DB scenarios delegated to local runner · dialogue `ses_e29049286ffeS0EcqftmYHyGNJ` · [request](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P18-T05](docs/tasks/p18-t05.md) Local search API: ranking, filters, snippets and ACL awareness — **IN_PROGRESS** · opencode-p18-t05 · remote lane; `local_connector/` only, DB-exempt, no file upload (that is P18-T06) · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P18-T06](docs/tasks/p18-t06.md) Explicit file fetch, version tracking and index deletion — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P18-T07](docs/tasks/p18-t07.md) Packaging, signing and auto-update for Windows, macOS and Linux — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`


## Deferred by explicit owner decision

- [P2-T08](docs/tasks/p2-t08.md) corporate/shared billing accounts — **DEFERRED** · product-owner · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c` · `P3` · `C4`
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

## [P9](docs/phases/p9-safe-activation.md)

P9's accepted outcomes and activation boundaries are summarized in the linked phase.

## [P10](docs/phases/p10-audit-hardening.md)

P10's accepted hardening outcomes and limitations are summarized in the linked phase.

## [P11](docs/phases/p11-structure.md)

Accepted P11 outcomes are in the linked parent phase; register new follow-ups in the execution queue.

## [P12](docs/phases/p12-domain-workspace-expansion.md)

Use the P12 cards in the execution queue or the linked parent plan.

## [P13](docs/phases/p13-business-foundation.md)

Shared foundation for business modules; use the P13 cards in the execution queue.

## [P14](docs/phases/p14-bitrix24-crm.md)

Bitrix24 CRM (TZ module 1); use the P14 cards in the execution queue.

## [P15](docs/phases/p15-communication-hub.md)

Unified communication hub (TZ module 2); use the P15 cards in the execution queue.

## [P16](docs/phases/p16-planner-calendar.md)

Planner tasks and calendars (TZ module 3); use the P16 cards in the execution queue.

## [P17](docs/phases/p17-onec-erp.md)

1C:Enterprise (TZ module 4); use the P17 cards in the execution queue.

## [P18](docs/phases/p18-local-file-connector.md)

Local file connector (TZ module 5); use the P18 cards in the execution queue.

## [P19](docs/phases/p19-document-management.md)

Document management (TZ module 6); use the P19 cards in the execution queue.

## [P20](docs/phases/p20-proactive-assistant.md)

Proactive assistant and unified workspace; use the P20 cards in the execution queue.

## [P21](docs/phases/p21-ai-operations.md)

Model catalogs, plan grants and dispatch-safety review; implementation is not activated.

## Questions

Q-2/Q-5 are ordered in the execution queue; Q-12 remains owner-deferred. Q-18 is closed
(owner decisions D1–D9, [record](docs/audit/q/q-18-owner-decisions.md)).
- [Q-19](docs/tasks/q-19.md) Local connector protocol: second-party security review and real-device evidence — **TODO** · open
  The P18-T01 protocol is accepted as a design; no line of it has been exercised by a running system. Tracked so acceptance is
  never read as verification.
The P6-T05 audit's two test follow-ups were accepted under [P6-T06](docs/audit/p6/p6-t06.md); the original audit remains historical evidence.
