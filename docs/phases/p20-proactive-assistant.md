# P20 — Proactive assistant and safe orchestration

[Product direction](../PRODUCT_PLAN.md#proactive-assistance) · [ROADMAP](../../ROADMAP.md#p20) · [owner request](https://app.notion.com/chat?t=40fa7457748980d094a100a930619250)

**Goal:** Help users find what needs attention, review decisions and organize bounded research using existing task, digest, permission, command and runtime contracts. These are planned capabilities, not shipped features or authorization to activate them.

## Plan

1. [P20-T01](../tasks/p20-t01.md) — plan and coordinate the owner-requested improvements.
2. [P20-T02](../tasks/p20-t02.md) — add evidence-linked triage and a morning brief to the existing social research/digest flow.
3. [P20-T03](../tasks/p20-t03.md) — aggregate pending decisions in an approval inbox over their existing authoritative ledgers.
4. [P20-T04](../tasks/p20-t04.md) — pilot bounded specialist delegation on the existing job and task-plan foundations.
5. [P20-T05](../tasks/p20-t05.md) — review-gated isolated browser pilot for authorized sources, only if a measured API gap remains.

The first two are product-facing additions to existing journeys; delegation and browser execution are later, independently gated work, not prerequisites. The VK pilot and its Q-5 method/profile gate remain unchanged. No P20 card authorizes a production schema change, live provider use, new infrastructure spend, deployment or external delivery. Coordinate T02 with P8-T29/T40/T41 and T03 with P15-T05 and P19-T04; T05 does not bypass P12-T14, Q-5, source permissions, provider controls or CAPTCHA.

## Inspiration and limits

The [CopilotKit OpenDuet README](https://github.com/CopilotKit/OpenDuet/blob/f52288fff5ccdbd3de428d573d9f026fce5ff480/README.md) describes mail triage, a brief, approval choices and per-agent computers. The separate [Anil-matcha Open Duet README](https://github.com/Anil-matcha/open-duet/blob/800c33ae8d195567ecbe73001c096d7acc9ec908/README.md) describes Today and lead/specialist teams; its own caveats include experimental delegation and computers. These are ideas, not proofs of our requirements, security or production readiness. No dependency, service or license adoption is selected.
