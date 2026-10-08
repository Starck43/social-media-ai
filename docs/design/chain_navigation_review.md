# Chain continuation links and origin-aware navigation

Base: dev commit `d42b98828547669246c88c401928d7597f2f0bbc`.
Target: dev only. No migration, production data rewrite or live LLM call.

## Changes

- Replace the faulty intersection-self comparison with Sørensen–Dice similarity
  on normalized/stemmed token sets. A single shared word no longer scores 1.
  Identical normalized topics/token reorderings score 1; empty/disjoint sets 0.
  The existing 0.85 threshold, source/workspace restriction and lookback remain.
- A chain ID may be assigned to the first analysis before a continuation exists.
  On the flat group list, show the chain link only when its actual opened
  timeline contains at least two analyses in the same tenant/source/period
  scope. Display `Цепочка · N анализ(ов)`. Count scoped rows once, before the
  membership/cross-filter slice, without a per-row DB query.
- Add `← К группам` alongside the existing breadcrumb. Both preserve the exact
  origin page, including sources/month/all and other grouping/filter state.
- Main-page and group/list chain links carry an encoded local return_to. The
  detail primary back control returns to the actual originating group, main
  page or chain list. Keep All chains as a secondary destination where needed.
- Preserve the group's own parent origin on the return trip, allowing the
  complete main → group → chain → group → main navigation round trip.
- Allow only local, read-only analytics return destinations. Reject external
  hosts/schemes, mutation paths, fragments, control characters, backslashes and
  oversized values; use the existing local fallback when invalid.

## Boundaries

This does not retroactively repair historical chain assignments, silently split
or merge chains, remove singleton IDs, change chain-list filters, add a topic
filter or restore sentiment/content_type enum axes. Existing direct chain
URLs and chain-list inspection remain available, including singleton seeds.
The >=2 continuation-link rule applies to the flat drill-down list. Counts
match the opened retrospective, not just rows visible in a sentiment/media/
entity/topic slice. Current inclusive date-window semantics are unchanged.

## Validation

- Focused chain/web suite: 60 passed. Covers one-word false matches, legitimate
  case-normalized reuse, singleton hiding, window-sensitive counts, exact
  source/month/all return state and safe handling of untrusted return URLs.
- Full suite: `961 passed, 1 skipped, 10 warnings in 217.42s (0:03:37)` on a freshly reset local PostgreSQL nav_test schema
  (`python -m pytest --no-cov -q`); existing deprecation warnings remain.
- git diff --check passed.
- No API.md, migration, token-routing or publication changes.

## File-by-file

| File | Change |
| --- | --- |
| app/services/ai/chain_resolver.py | Genuine token-set overlap instead of intersection-self similarity |
| app/web/analytics.py | Timeline counts, local return validation and complete origin propagation |
| app/web/templates/web/analytics_group.html | Explicit group back button and counted multi-entry chain links |
| app/web/templates/web/analytics_chain_detail.html | Origin-aware primary back control plus secondary All chains |
| tests/test_chain_resolver.py | Similarity and real resolver regression checks |
| tests/test_web_analytics.py | Scope/count and exact/safe navigation regressions; fixture now has real continuation |
| docs/ANALYTICS_AGGREGATION_SYSTEM.md | Similarity definition, singleton semantics and return contract |
