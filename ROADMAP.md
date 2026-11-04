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

- [P8-T33](docs/tasks/p8-t33.md) Additional VK search categories — **IN_PROGRESS** · notion-ai-p8-t33 · [chat](https://app.notion.com/chat?t=40a3d3cc6f4080648d4000a9e3177cc4) · `P2` · `C4`
- [P8-T46](docs/tasks/p8-t46.md) VK video search category — **IN_PROGRESS** · notion-ai-p8-t46 · [chat](https://app.notion.com/chat?t=40bd5248ae66806fba4a00a9d3a3af96) · `P2` · `C3`
- [P8-T47](docs/tasks/p8-t47.md) VK apps/games search category — **IN_PROGRESS** · notion-ai-p8-t47 · [chat](https://app.notion.com/chat?t=40bd5248ae66806fba4a00a9d3a3af96) · `P2` · `C3`
- [P8-T48](docs/tasks/p8-t48.md) VK owner-scoped market items category — **IN_PROGRESS** · notion-ai-p8-t48 · [chat](https://app.notion.com/chat?t=40bd5248ae66806fba4a00a9d3a3af96) · `P2` · `C3`
- [P8-T49](docs/tasks/p8-t49.md) VK music search category — **IN_PROGRESS** · notion-ai-p8-t49 · [chat](https://app.notion.com/chat?t=40bd5248ae66806fba4a00a9d3a3af96) · `P2` · `C3`
- [P2-T09](docs/tasks/p2-t09.md) production billing verification — **TODO** · unassigned · — · `P2` · `C4`
- [Q-2](docs/tasks/q-2.md) VK source 2939 origin and missing media — **OPEN** · codex-vk-raw-audit · — · `P2` · `C3`
- [Q-17](docs/tasks/q-17.md) Web test-suite debt in `tests/unit_web/` — **OPEN** · unassigned · raised while closing [P12-T16](docs/audit/p12-t16.md) · `P2` · `C3`

- [P12-T12](docs/tasks/p12-t12.md) File workspace: originals-first navigation, compact agent memory and safe file actions — **IN_PROGRESS** · codex-p12-pr-completion ([PR #327](https://github.com/Starck43/social-media-ai/pull/327)) · [owner-authorized completion](https://github.com/Starck43/social-media-ai/pull/327#issuecomment-6609629474) · `P2` · `C5`
- [P8-T34](docs/tasks/p8-t34.md) Yandex Disk read-only file pilot — **IN_PROGRESS** · codex-p12-pr-completion ([PR #327](https://github.com/Starck43/social-media-ai/pull/327)) · [owner-authorized completion](https://github.com/Starck43/social-media-ai/pull/327#issuecomment-6609629474) · [PR #313](https://github.com/Starck43/social-media-ai/pull/313) · [audit](docs/audit/p8-t34.md) · `P2` · `C4`
- [P12-T15](docs/tasks/p12-t15.md) User-managed folder permissions and safe file actions — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40c071f39db6802e935c00a98e78df77) · `P2` · `C4`

Business modules P13–P19 ([adapted TZ and review](docs/BUSINESS_MODULES.md)); bands set by owner decision D1 ([Q-18](docs/audit/q-18-owner-decisions.md)):

- [P14-T07](docs/tasks/p14-t07.md) Bitrix24 as a global application with tenant-scoped portal settings instead of deployment client credentials — **IN_PROGRESS** · remote-agent (`feat/p14-t05-bitrix-global-app`) · owner decision 2026-11-04: the OAuth client secret is not carried in environment configuration · `P2` · `C3`
- [P14-T08](docs/tasks/p14-t08.md) Trusted on-premise portal hosts administered at runtime, with a per-host egress authorization — **TODO** · unclaimed · owner request 2026-11-04: an on-premise portal must not require a deployment edit · `P2` · `C3`

- [P14-T05](docs/tasks/p14-t05.md) Approved greeting delivery via email, Telegram and MAX — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P2` · `C3`
- [P14-T06](docs/tasks/p14-t06.md) Batch messaging with personalization, consent and unsubscribe — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P2` · `C4`

### Priority P3

- [P15-T01](docs/tasks/p15-t01.md) Email connector: IMAP incremental read sync — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P15-T02](docs/tasks/p15-t02.md) Unified message store and channel ingestion (Telegram, MAX, email) — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P15-T03](docs/tasks/p15-t03.md) Identity reconciliation: suggestions, manual merge and unmerge — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P15-T04](docs/tasks/p15-t04.md) Unified inbox: threads, filters, search and read state — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P15-T05](docs/tasks/p15-t05.md) Persistent outbox, scheduling and delivery tracking — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P15-T06](docs/tasks/p15-t06.md) Channel senders: SMTP, Telegram bot and MAX bot capabilities — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P15-T07](docs/tasks/p15-t07.md) Channel recommendation with confidence score — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P15-T08](docs/tasks/p15-t08.md) Reply from the unified interface — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P16-T01](docs/tasks/p16-t01.md) Planner tasks: model, CRUD, subtasks and recurrence — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P16-T02](docs/tasks/p16-t02.md) Reminders, snooze and multi-channel notifications — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P16-T03](docs/tasks/p16-t03.md) Calendar connections and Google Calendar two-way sync — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C5`
- [P16-T04](docs/tasks/p16-t04.md) Yandex Calendar two-way sync over CalDAV — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P16-T05](docs/tasks/p16-t05.md) Event planning: birthday tasks and templates — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P17-T01](docs/tasks/p17-t01.md) 1C connection: OData/HTTP-service endpoints, credentials and health — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P17-T02](docs/tasks/p17-t02.md) 1C read-only data extraction from catalogs, documents and registers — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P17-T03](docs/tasks/p17-t03.md) Natural-language queries, result formatting, export and history — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P17-T04](docs/tasks/p17-t04.md) 1C write commands with preview, approval and audit — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C5`
- [P17-T05](docs/tasks/p17-t05.md) 1C role-based access policy and audit views — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P18-T03](docs/tasks/p18-t03.md) Desktop connector core: folder selection, safe paths and incremental index — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C5`
- [P18-T04](docs/tasks/p18-t04.md) Text extraction and OCR with resource limits — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P18-T05](docs/tasks/p18-t05.md) Local search API: ranking, filters, snippets and ACL awareness — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P18-T06](docs/tasks/p18-t06.md) Explicit file fetch, version tracking and index deletion — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P18-T07](docs/tasks/p18-t07.md) Packaging, signing and auto-update for Windows, macOS and Linux — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P19-T01](docs/tasks/p19-t01.md) Document model extension: types, links, templates and status machine — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P19-T02](docs/tasks/p19-t02.md) Templates and AI drafting with evidence and unsupported-claim marking — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P19-T03](docs/tasks/p19-t03.md) Version history, diff and restore — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P19-T04](docs/tasks/p19-t04.md) Document approval workflow bound to an immutable revision hash — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C4`
- [P19-T05](docs/tasks/p19-t05.md) Export to DOCX/PDF and delivery via outbox — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C3`
- [P19-T06](docs/tasks/p19-t06.md) Document search — **TODO** · unassigned · [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f) · `P3` · `C2`


## Deferred by explicit owner decision

- [P2-T08](docs/tasks/p2-t08.md) corporate/shared billing accounts — **DEFERRED** · product-owner · `codex resume 01a17835-5608-7112-9a99-af8b09ba405c` · `P3` · `C4`
- [P2-T15](docs/tasks/p2-t15.md) extensible capability entitlements foundation — **DEFERRED** (owner: tests/validation deferred to project end, 2026-10-27) · notion-ai · integrated 2026-10-27 as merge 67acbd53 (0117 re-parented onto 0116, [handoff + merge](https://github.com/Starck43/social-media-ai/pull/253#issuecomment-6428493023)); validation gates still open · `P3` · `C4`
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

## Questions

Q-2/Q-5 are ordered in the execution queue; Q-12 remains owner-deferred. Q-18 is closed
(owner decisions D1–D9, [record](docs/audit/q-18-owner-decisions.md)).
- [Q-19](docs/tasks/q-19.md) Local connector protocol: second-party security review and real-device evidence — **TODO** · open
  The P18-T01 protocol is accepted as a design; no line of it has been exercised by a running system. Tracked so acceptance is
  never read as verification.
The P6-T05 audit's two test follow-ups were accepted under [P6-T06](docs/audit/p6-t06.md); the original audit remains historical evidence.
