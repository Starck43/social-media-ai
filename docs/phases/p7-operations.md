# P7 — Reproducible operation

[Product direction](../PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p7) · [full historical notes](../audit/phase-p7-history.md)

**Goal:** Installation, migrations, runtime ownership and recovery are reproducible from evidence.

**Plan — active task cards:**

- [P7-T02](../tasks/p7-t02.md) — One staging-backed operations proof
- [P7-T07](../tasks/p7-t07.md) — Clarify local development versus server staging/production startup commands — IN_PROGRESS (notion-ai; implementation in `dev` via [`c3f57bc`](https://github.com/Starck43/social-media-ai/commit/c3f57bcfcdde65abe7e6d14b1f22ba62427506b2) and [`98a356d`](https://github.com/Starck43/social-media-ai/commit/98a356dcff44316238e9affd9f4eec27721449fa); [PR #287 merged as `db1e5dd3`](https://github.com/Starck43/social-media-ai/pull/287); [audit](../audit/p7-t07.md) returned two correctable findings)

**Accepted history:**

- [P7-T03](../audit/phase-p7-history.md#p7-t03) — use canonical domain model imports and direct manager registration
- [P7-T04](../audit/phase-p7-history.md#p7-t04) — document routine OpenCode routing and separate pytest route
- [P7-T01](../audit/p7-t01.md) — loopback-only database binding before production release · 2026-10-25 · `526cfd2`
- [P7-T05](../audit/p7-t05.md) — prepare isolated staging and database recovery · DONE 2026-10-29 · accepted review source `e8099a150283e86fa2258360fc8151532acd8ed8`; PR #280 merge `045150db19fc402de5b51c18b4fe037388f0846d`
- [P7-T06](../audit/p7-t06.md) — prepare a gated single-VPS deployment path · DONE 2026-10-29 · accepted source `e8099a150283e86fa2258360fc8151532acd8ed8`; PR #281 merge `02dde506b91b71431cd713c7121a434eae026f10`

**Phase status:** Active phase; close only after every plan item is accepted.
