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






### Priority P1


- [P4-T01](docs/tasks/p4-t01.md) staging operator-recovery evidence — **BLOCKED** · unassigned · — · `P1` · `C3`
- [P7-T02](docs/tasks/p7-t02.md) One staging-backed operations proof — **BLOCKED** · unassigned · — · `P1` · `C4`
- [P6-T01](docs/tasks/p6-t01.md) Deploy-gated retention rollout — **BLOCKED** · product-owner · — · `P1` · `C3`

- [P22-T14](docs/tasks/p22-t14.md) Task-scoped agent chat and shared transcript UI — **IN_PROGRESS** · Codex · `codex resume 01a21254-6a51-7bb1-a259-488699b97b91` · `P1` · `C4`
- [P22-T18](docs/tasks/p22-t18.md) Task filters, calendar and rolling run results — **TODO** · unassigned · — · `P1` · `C4`
- [P22-T08](docs/tasks/p22-t08.md) Unify task navigation, labels, creation and detail views — **TODO** · unassigned · `codex resume 01a1fb41-51c9-76a1-8771-93536f57a54b` · `P1` · `C4`
- [P22-T10](docs/tasks/p22-t10.md) Correspondence task and sender-grouped Messages — **TODO** · unassigned · `codex resume 01a1fb41-51c9-76a1-8771-93536f57a54b` · `P1` · `C4`

### Priority P2





- [P2-T09](docs/tasks/p2-t09.md) production billing verification — **TODO** (gate: production activation/cutover; not claimable before it) · unassigned · — · `P2` · `C4`



- [P12-T12](docs/tasks/p12-t12.md) File workspace: residual slices S1 (lifecycle), S2 (typed actions), and S4 (measurement harness) delivered; remaining scope stays gated (S3 version freshness, P8-T34 byte authority, local-only execution with P18, activation, D21 mutations) — **TODO** (gate: owner activation / P8-T34 resume / consumer contract request; not claimable before it; S1/S2 delivered 2026-11-19, S4 delivered 2026-11-21, increment accepted 2026-11-03 `6433ae6a`/`48caea61`) · unassigned · — · [residual slices](docs/tasks/p12-t12.md#residual-slices-unblock-or-owner-prioritize-first) · [increment audit](docs/audit/p12/p12-t12-increment.md) · `P2` · `C5`
- [P8-T34](docs/tasks/p8-t34.md) Yandex Disk read-only file pilot and Settings OAuth — **IN_PROGRESS** · Codex · `codex resume 01a1f7d1-8006-75a3-ac37-05c3d89df357` · [audit](docs/audit/p8/p8-t34.md) · `P2` · `C4`

- [P20-T19](docs/tasks/p20-t19.md) Workspace prototype review and control consistency — **IN_PROGRESS** · OpenCode · `opencode --session ses_debebc801ffe763bmMf7W9xKfz` · `P2` · `C5`
- [P20-T20](docs/tasks/p20-t20.md) Today dashboard, decision previews and quick replies — **IN_PROGRESS** · OpenCode · owner-directed direct `dev` · `P2` · `C3`

- [P22-T09](docs/tasks/p22-t09.md) Admin connector setup and task operations — **TODO** · unassigned · `codex resume 01a1fb41-51c9-76a1-8771-93536f57a54b` · `P2` · `C3`
- [P22-T15](docs/tasks/p22-t15.md) Turn commitments into dated tasks with reminders — **TODO** · unassigned · — · `P2` · `C4`
- [P22-T20](docs/tasks/p22-t20.md) Page older chat messages and saved task conversations — **TODO** · unassigned · `codex resume 01a21254-6a51-7bb1-a259-488699b97b91` · `P2` · `C4`
- [P22-T21](docs/tasks/p22-t21.md) Budget and compact agent conversation context — **TODO** · unassigned · `codex resume 01a21254-6a51-7bb1-a259-488699b97b91` · `P2` · `C4`
- [P22-T11](docs/tasks/p22-t11.md) External tasks and team tracker commands — **TODO** · unassigned · `codex resume 01a1fb41-51c9-76a1-8771-93536f57a54b` · `P2` · `C4`
- [P22-T12](docs/tasks/p22-t12.md) Workspace cutover, reconciliation and legacy retirement — **TODO** · unassigned · `codex resume 01a1fb41-51c9-76a1-8771-93536f57a54b` · `P2` · `C5`

Registered tooling follow-ups (not phase work; repo-level hygiene):



Business modules P13–P19 ([adapted TZ and review](docs/product/BUSINESS_MODULES.md)); bands set by owner decision D1 ([Q-18](docs/audit/q/q-18-owner-decisions.md)):

- [P14-T08](docs/tasks/p14-t08.md) Bitrix24 on-premise authorization form on top of the P14-T09 seam — **DEFERRED** (owner, 2026-11-04: no on-premise customer; resumes with P14-T09) · `P2` · `C3`


### Priority P3

- [P20-T05](docs/tasks/p20-t05.md) Review-gated isolated browser execution pilot — **DEFERRED** (owner, 2026-11-14: P20-T12 named no source where an official API and plain fetch fail, and disabled the unconfined Chromium fallback; resume only with an evidenced named source + a new owner decision on infrastructure and cost) · unassigned · [request](https://app.notion.com/chat?t=40fa7457748980d094a100a930619250) · `P3` · `C5`


- [P22-T13](docs/tasks/p22-t13.md) Jira issue adapter and Slack task context — **TODO** · unassigned · `codex resume 01a1fb41-51c9-76a1-8771-93536f57a54b` · `P3` · `C4`

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

## [P22](docs/phases/p22-universal-tasks.md)

Universal tasks, connectors, decisions and cross-surface migration.

## Questions

Q-12 remains owner-deferred. Q-18 is closed (owner decisions D1–D9,
[record](docs/audit/q/q-18-owner-decisions.md)); Q-5 is closed with
[live VK search evidence](docs/audit/q/q-5.md); Q-2 is closed with
[live wall-origin and media evidence](docs/audit/q/q-2.md).
- [Q-19](docs/tasks/q-19.md) Local connector protocol: second-party security review and real-device evidence — **TODO** · open
  The P18-T01 protocol is accepted as a design; no line of it has been exercised by a running system. Tracked so acceptance is
  never read as verification.
- [Q-22](docs/tasks/q-22.md) Local connector: native platform evidence and Windows runtime enablement — **TODO** · owner · P18-T07 accepted with its platform gates recorded rather than closed (owner instruction 2026-11-15); owner asked to revisit at the end of the project · [audit](docs/audit/p18/p18-t07.md) · accepted source `9ca9e47d`, merge `ad7ae24d` · `P3` · `C4`
The P6-T05 audit's two test follow-ups were accepted under [P6-T06](docs/audit/p6/p6-t06.md); the original audit remains historical evidence.
