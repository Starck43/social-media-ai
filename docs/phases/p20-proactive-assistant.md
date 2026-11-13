# P20 — Proactive assistant and unified workspace

[Product direction](../product/PRODUCT_PLAN.md#proactive-assistance-and-unified-workspace) · [ROADMAP](../../ROADMAP.md#p20) · [Workspace design](../product/WORKSPACE_UX.md) · [owner request](https://app.notion.com/chat?t=40fa7457748980d094a100a930619250)

**Goal:** Make the product easy to use daily, alone or in a team: four clear entry points («Сегодня», chat, «Входящие», «Задачи»), attention and decisions gathered read-time from the owning modules, agent help beside every object, and bounded research. These are planned capabilities, not shipped features or authorization to activate them.

## Plan

1. P20-T01 — plan, audit open work and coordinate. **DONE** — [../audit/p20-t01.md](../audit/p20/p20-t01.md).
2. ~~P20-T02 — social triage, the shared attention-item contract and a morning brief.~~ **DONE 2026-11-12**, accepted source `fe68a25da530d9d86818b90a053a7973936f4cbc`, merge `ccd85a4ba3efb879bafd581053bc8aeb9b00b1d5` — [audit](../audit/p20/p20-t02.md).
3. ~~P20-T03 — one decision projection over existing approval ledgers.~~ **DONE 2026-11-10**, accepted source `a8afa2eb3a577b24eb803155af3d63681776f343`, merge `a53e1741c2c83d6db7e4efa40becbb85c6c323d0` — [audit](../audit/p20/p20-t03.md).
4. P20-T04 — bounded specialist delegation for research. **DONE 2026-11-11** — [../audit/p20-t04.md](../audit/p20/p20-t04.md).
5. [P20-T05](../tasks/p20-t05.md) — review-gated isolated browser pilot, only after T12 shows a measured gap.
6. ~~P20-T06 — navigation regrouping and mobile entry points.~~ **DONE 2026-11-07**, accepted source `aa9f4e87`, merge `efcbc8b9` (+ regression fix `e4fe0a33`) — [audit](../audit/p20/p20-t06.md).
7. ~~P20-T07 — «Сегодня» home as a read-time projection.~~ **DONE 2026-11-08**, accepted source `76da862`, merge `bc550ac5` — [audit](../audit/p20/p20-t07.md).
8. ~~P20-T08 — chat context panel, inline cards and chat dock.~~ **DONE 2026-11-08**, accepted source `cc121ba`, merge `b86d5b45` — [audit](../audit/p20/p20-t08.md).
9. ~~P20-T09 — inbox triage and agent-suggested replies.~~ **DONE 2026-11-10**, accepted source `1362086c`, merged `8af6c870` (migration `0151`) — [audit](../audit/p20/p20-t09.md).
10. [P20-T10](../audit/p20/p20-t10.md) — **DONE 2026-11-10**, unified task hub, calendar projection and confirmed linked assignments; accepted `d2817412a68e0fc043af3b17cebc4953ebff18f9`.
11. [P20-T11](../audit/p20/p20-t11.md) — **DONE 2026-11-11**: Bitrix24 read-only assigned/watched task sources in Tasks and Today; accepted `4fcf66959a32692199f6bcea0d37c0ab7192521e`.
12. ~~P20-T12 — web research source, search and public page fetch.~~ **DONE 2026-11-11**, accepted source `bf180e4b3acd4bb9e944852835d73e4943edf323`, merge `d17c898031d7cdbd062a04254091a64a4a3caf58` — [audit](../audit/p20/p20-t12.md).
13. P20-T13 — bounded web usability and keyboard/mobile hardening, preserving the existing theme and owning-service contracts. **DONE** — [../audit/p20-t13.md](../audit/p20/p20-t13.md).

## Waves

| Wave | Tasks | Needs |
| --- | --- | --- |
| A — existing product UX | T06 → T07, T08 | Only accepted foundations |
| B — attention and decisions | T02, T03; T09 after P15-T04/T08 | Owning modules for each projection |
| C — tasks and teams | T10 after P16-T01/T03; T11 after P16-T04 | Planner, calendar, source registry |
| D — research | T12, then T04; T05 only on a measured gap | Owner provider/spend choices |

Wave A improves what already exists and is not blocked by P15–P19. Later projections plug into «Сегодня» and chat through their own tasks; no task here builds a second ledger, notification sender, task store or calendar. The VK pilot and its Q-5 gate remain unchanged. No P20 card authorizes a production schema change, live provider use, new infrastructure spend, deployment or external delivery. T05 does not bypass P12-T14, Q-5, source permissions, provider controls or CAPTCHA. Future deployment modes and clients are recorded separately in [PLATFORM_TOPOLOGY](../product/PLATFORM_TOPOLOGY.md) and are not part of this phase.

## Inspiration and limits

The [CopilotKit OpenDuet README](https://github.com/CopilotKit/OpenDuet/blob/f52288fff5ccdbd3de428d573d9f026fce5ff480/README.md) describes mail triage, a brief, approval choices and per-agent computers. The separate [Anil-matcha Open Duet README](https://github.com/Anil-matcha/open-duet/blob/800c33ae8d195567ecbe73001c096d7acc9ec908/README.md) describes Today and lead/specialist teams; its own caveats include experimental delegation and computers. OpenDuet centers on messages and notifications; here tasks and connected business objects are equal entry points, and the inbox is one of them. These are ideas, not proofs of our requirements, security or production readiness. No dependency, service or license adoption is selected.
