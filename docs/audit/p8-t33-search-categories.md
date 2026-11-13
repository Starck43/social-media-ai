# P8-T33 / T46 / T47 / T48 — VK search-result categories, mocked contract slice

- **Date:** 2026-11-11
- **Tasks:** [P8-T33](../archive/tasks/p8-t33.md) (registry and `people`), [P8-T46](../archive/tasks/p8-t46.md) (video),
  [P8-T47](../archive/tasks/p8-t47.md) (apps/games), [P8-T48](../archive/tasks/p8-t48.md) (owner-scoped market)
- **Integration:** [#295](https://github.com/Starck43/social-media-ai/pull/295) merge
  `9857f411` (registry, `people`, five-surface catalog) and
  [#298](https://github.com/Starck43/social-media-ai/pull/298) merge `8a687f4f` (video, apps,
  market mocked contracts); both merge commits verified ancestors of `origin/dev`.
- **Status:** accepted as the mocked slice; all four rows removed from [ROADMAP](../../ROADMAP.md).
  Live enablement is owned by [Q-5](../tasks/q-5.md) and is **not** accepted here.

## What is accepted

One evidence-gated registry, `app/services/collection/search_categories.py`, and one read model,
`category_catalog()`, behind five surfaces (web, API, CLI, admin, agent):

- `people` → official `users.search` via the existing `search_people`, typed people results with
  category/method provenance, never Sources;
- `video` → `video.search`, `apps` → `apps.getCatalog` (with the official `q` parameter; the
  documented quota is 60 requests/minute per IP or id), `market` → `market.search` with a required
  numeric owner/community scope;
- all four are `mocked`: implemented and contract-tested, not executable and not shown as
  available. Only `enabled` executes; `resolve_categories` aggregates only enabled categories for
  `all`, returns the rest with bounded reasons, and fails closed on unknown ids;
- a caller-supplied category object cannot open the gate — `search_category` re-resolves the
  canonical registry record;
- clips and channels stay `unsupported` with reasons; music moved to `unsupported` on 2026-11-11
  ([audit](p8-t49.md)).

## Evidence re-checked for this acceptance

- `pytest tests/collection/test_p8_t46_t49_search_contracts.py
  tests/collection/test_p8_t46_t49_surface_parity.py tests/collection/test_p8_t33_search_categories.py
  tests/collection/test_p8_t33_surface_parity.py tests/collection/test_vk_collection_adapter.py`
  → **217 passed**, 2 warnings, at the accepted revision on `dev` (2026-11-11). Source-only: the
  modules are marked `__source_only__` with fail-closed tripwires for sockets, DB and credential
  resolution; no live VK call and no DB write.
- The delivered paths exist in `origin/dev` and are the accepted blobs; both merge commits are
  ancestors of `origin/dev`.
- Contract coverage re-read from `docs/checks/p8-t46-t49.md`: typed success per method with exact
  parameters, empty/partial pages, invalid ids, provider overdelivery, bounded paging (page 100,
  search ceiling 1000), bounded quota loop (codes 6/9/29, HTTP 429 `Retry-After`), closed gates and
  five-surface handler parity. That file records 79 failures fixed by the implementation, which is
  why the contracts are trusted rather than merely asserted.

## What is explicitly not accepted

Live capability. `Q-5` still owns the application-profile and method-permission evidence for the
global-search methods; until it answers, no category may become `enabled` from code. This audit
accepts a non-user-visible contract, not the feature. A read-only probe that measures all four
methods in one run is added under Q-5 as its own PR; no category status changes from it.

## Deviation

The executor and the accepting auditor were the same agent, on the owner's explicit instruction of
2026-11-11 ("согласен со всем"). Recorded as a deviation from independent review: the acceptance
rests on re-running the shipped checks and re-reading the contracts, not on a second reviewer.

## Rollback

Revert `8a687f4f` and `9857f411`. The registry module and its tests go together; no schema, data or
user-visible surface is involved.
