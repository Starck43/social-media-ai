# P20 — Proactive assistant and unified workspace

[Product direction](../PRODUCT_PLAN.md#proactive-assistance-and-unified-workspace) · [ROADMAP](../../ROADMAP.md#p20) · [Workspace design](../WORKSPACE_UX.md) · [owner request](https://app.notion.com/chat?t=40fa7457748980d094a100a930619250)

**Goal:** Make the product easy to use daily, alone or in a team: four clear entry points («Сегодня», chat, «Входящие», «Задачи»), attention and decisions gathered read-time from the owning modules, agent help beside every object, and bounded research. These are planned capabilities, not shipped features or authorization to activate them.

## Plan

1. [P20-T01](../tasks/p20-t01.md) — plan, audit open work and coordinate.
2. [P20-T02](../tasks/p20-t02.md) — social triage, the shared attention-item contract and a morning brief.
3. [P20-T03](../tasks/p20-t03.md) — one decision projection over existing approval ledgers.
4. [P20-T04](../tasks/p20-t04.md) — bounded specialist delegation for research.
5. [P20-T05](../tasks/p20-t05.md) — review-gated isolated browser pilot, only after T12 shows a measured gap.
6. ~~P20-T06 — navigation regrouping and mobile entry points.~~ **DONE 2026-11-07**, accepted source `aa9f4e87`, merge `efcbc8b9` (+ regression fix `e4fe0a33`) — [audit](../audit/p20-t06.md).
7. [P20-T07](../tasks/p20-t07.md) — «Сегодня» home as a read-time projection.
8. [P20-T08](../tasks/p20-t08.md) — chat context panel, inline cards and chat dock.
9. [P20-T09](../tasks/p20-t09.md) — inbox triage and agent-suggested replies.
10. [P20-T10](../tasks/p20-t10.md) — unified task surface with calendar view.
11. [P20-T11](../tasks/p20-t11.md) — team work through connected task systems.
12. [P20-T12](../tasks/p20-t12.md) — web search and public page fetch.

## Waves

| Wave | Tasks | Needs |
| --- | --- | --- |
| A — existing product UX | T06 → T07, T08 | Only accepted foundations |
| B — attention and decisions | T02, T03; T09 after P15-T04/T08 | Owning modules for each projection |
| C — tasks and teams | T10 after P16-T01/T03; T11 after P16-T04 | Planner, calendar, source registry |
| D — research | T12, then T04; T05 only on a measured gap | Owner provider/spend choices |

Wave A improves what already exists and is not blocked by P15–P19. Later projections plug into «Сегодня» and chat through their own tasks; no task here builds a second ledger, notification sender, task store or calendar. The VK pilot and its Q-5 gate remain unchanged. No P20 card authorizes a production schema change, live provider use, new infrastructure spend, deployment or external delivery. T05 does not bypass P12-T14, Q-5, source permissions, provider controls or CAPTCHA. Future deployment modes and clients are recorded separately in [PLATFORM_TOPOLOGY](../PLATFORM_TOPOLOGY.md) and are not part of this phase.

## Inspiration and limits

The [CopilotKit OpenDuet README](https://github.com/CopilotKit/OpenDuet/blob/f52288fff5ccdbd3de428d573d9f026fce5ff480/README.md) describes mail triage, a brief, approval choices and per-agent computers. The separate [Anil-matcha Open Duet README](https://github.com/Anil-matcha/open-duet/blob/800c33ae8d195567ecbe73001c096d7acc9ec908/README.md) describes Today and lead/specialist teams; its own caveats include experimental delegation and computers. OpenDuet centers on messages and notifications; here tasks and connected business objects are equal entry points, and the inbox is one of them. These are ideas, not proofs of our requirements, security or production readiness. No dependency, service or license adoption is selected.
